"""
视频自动剪辑引擎 v2.0

核心能力：
1. 脚本解析 — 从 daily-script .md 提取时间线标注
2. 素材对齐 — Whisper 转录口播素材 → 与脚本文本对齐
3. FFmpeg 组装 — 裁剪/变速/字幕/画面放大/BGM闪避混音
4. 分步审核 — 转录校对 → 剪辑计划 → 用户确认 → 渲染 → 反馈迭代

用法：
    py -3 video_editor.py <脚本路径> <素材路径>                    # 全自动
    py -3 video_editor.py <素材路径> --transcribe-only             # 只转录
    py -3 video_editor.py <脚本路径> <素材路径> --plan-only        # 只出计划
    py -3 video_editor.py <脚本路径> <素材路径> --align-only       # 只对齐
    py -3 video_editor.py <脚本路径> <素材路径> --font xxx.ttf     # 指定字体
    py -3 video_editor.py <脚本路径> <素材路径> --bgm xxx.mp3      # 带BGM
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


# ======================================================================
# 1. 脚本解析
# ======================================================================

def _annotation(body: str, label: str) -> Optional[str]:
    """读取单个方括号标注。"""
    match = re.search(rf'\[{re.escape(label)}:\s*([^\]]+)\]', body)
    return match.group(1).strip() if match else None


def _timecode_seconds(value: Optional[str]) -> Optional[tuple[float, float]]:
    """把 00:03-01:12.5 转成秒数区间。"""
    if not value:
        return None
    match = re.fullmatch(r'\s*(\d{1,2}):(\d{2}(?:\.\d+)?)\s*[-–—]\s*(\d{1,2}):(\d{2}(?:\.\d+)?)\s*', value)
    if not match:
        return None
    start = int(match.group(1)) * 60 + float(match.group(2))
    end = int(match.group(3)) * 60 + float(match.group(4))
    return start, end


def parse_script(script_path: str) -> list[dict]:
    """解析 daily-script，提取逐拍的叙事、情绪、画面和声音标注。"""
    with open(script_path, encoding='utf-8') as f:
        content = f.read()

    if content.startswith('---'):
        parts = content.split('---', 2)
        content = parts[2] if len(parts) > 2 else content

    segments = []
    section_blocks = re.split(r'\n## ', content)
    for block in section_blocks:
        if not block.strip():
            continue
        lines = block.strip().split('\n')
        title = lines[0].strip().lstrip('#').strip()
        body = '\n'.join(lines[1:]).strip()
        if not body:
            continue

        annotations = {
            label: _annotation(body, label)
            for label in (
                '时间', '叙事动作', '情绪', '画面', '字幕叠加', '语速',
                '停顿', '声音', '转场理由', '主强调', '章节标题'
            )
        }
        planned_range = _timecode_seconds(annotations['时间'])

        text = body
        for label in annotations:
            text = re.sub(rf'\[{re.escape(label)}:[^\]]*\]', '', text)
        text = text.strip()

        if not text:
            continue

        segments.append({
            'section': title,
            'text': text,
            'timecode': annotations['时间'],
            'planned_start': planned_range[0] if planned_range else None,
            'planned_end': planned_range[1] if planned_range else None,
            'narrative_action': annotations['叙事动作'],
            'emotion': annotations['情绪'],
            'visual': annotations['画面'],
            'subtitle_overlay': annotations['字幕叠加'],
            'speed': annotations['语速'] or '正常',
            'pause': annotations['停顿'],
            'sound': annotations['声音'],
            'transition_reason': annotations['转场理由'],
            'primary_emphasis': annotations['主强调'],
            'chapter_title': annotations['章节标题'],
            'emphasis': title == '金句' or (annotations['字幕叠加'] is not None),
        })

    return segments


# ======================================================================
# 2. 素材对齐 (Whisper)
# ======================================================================

def transcribe_footage(video_path: str, model_name: str = 'base') -> list[dict]:
    """Whisper 转写素材，返回词级时间戳列表。"""
    import whisper
    model = whisper.load_model(model_name)
    result = model.transcribe(video_path, language='zh', word_timestamps=True)

    words = []
    for seg in result.get('segments', []):
        if not seg.get('words'):
            words.append({
                'word': seg['text'].strip(),
                'start': seg['start'],
                'end': seg['end'],
            })
            continue
        for w in seg['words']:
            words.append({
                'word': w['word'].strip(),
                'start': w['start'],
                'end': w['end'],
            })
    return words


def detect_silences(words: list[dict], min_gap: float = 1.0) -> list[tuple[float, float]]:
    """检测长时间停顿（≥ min_gap 秒），返回 [(start, end), ...]。"""
    silences = []
    for i in range(len(words) - 1):
        gap = words[i + 1]['start'] - words[i]['end']
        if gap >= min_gap:
            silences.append((words[i]['end'], words[i + 1]['start']))
    return silences


def simple_text_match(script_text: str, whisper_words: list[dict]) -> tuple[float, float]:
    """在 whisper 转写中定位脚本文本的时间区间。"""
    clean_script = re.sub(r'[^一-鿿\w]', '', script_text)
    if len(clean_script) < 3:
        return (0.0, 0.0)

    windows = []
    for i, w in enumerate(whisper_words):
        window_text = ''
        window_start = w['start']
        window_end = w['end']
        for j in range(i, min(i + 200, len(whisper_words))):
            window_text += whisper_words[j]['word']
            window_end = whisper_words[j]['end']
            clean_window = re.sub(r'[^一-鿿\w]', '', window_text)
            if len(clean_window) >= len(clean_script):
                break
        else:
            continue
        clean_window = re.sub(r'[^一-鿿\w]', '', window_text)
        match_len = _lcs_length(clean_script, clean_window)
        ratio = match_len / max(len(clean_script), 1)
        windows.append((ratio, window_start, window_end, i))

    if not windows:
        return (0.0, 0.0)

    windows.sort(reverse=True)
    best_ratio, t_start, t_end, _ = windows[0]

    if best_ratio < 0.3:
        print(f'  ⚠ 匹配度低 ({best_ratio:.1%})，可能需要手动调整')

    return (t_start, t_end)


def _lcs_length(a: str, b: str) -> int:
    """最长公共子序列长度。"""
    m, n = min(len(a), 300), min(len(b), 600)
    a, b = a[:m], b[:n]
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]


def words_to_subtitles(words: list[dict], time_ranges: list[tuple[float, float]],
                       max_chars: int = 20,
                       script_texts: Optional[list[str]] = None) -> list[dict]:
    """将 Whisper 词级时间戳转为字幕事件，精确同步语音。

    若提供 script_texts，则用脚本文本替换 Whisper 文本，保留 Whisper 时间轴。
    返回: [{'text': str, 'start': float, 'end': float}, ...]
    """
    events = []
    for t_start, t_end in time_ranges:
        seg_words = [w for w in words
                     if w['start'] >= t_start - 0.15 and w['end'] <= t_end + 0.15]
        if not seg_words:
            continue

        line_words = []
        line_start = seg_words[0]['start']
        line_chars = 0

        for w in seg_words:
            line_chars += len(w['word'])
            line_words.append(w)
            word_text = w['word'].strip()

            should_break = (word_text and word_text[-1] in '，。！？、') or line_chars >= max_chars
            if should_break:
                text = ''.join(lw['word'] for lw in line_words)
                events.append({
                    'text': text,
                    'start': line_start,
                    'end': w['end'],
                })
                line_words = []
                line_start = w['end']
                line_chars = 0

        if line_words:
            text = ''.join(lw['word'] for lw in line_words)
            events.append({
                'text': text,
                'start': line_start,
                'end': line_words[-1]['end'],
            })

    # 若提供了脚本文字，映射到时间槽上
    if script_texts:
        events = _apply_script_texts(events, script_texts)

    # 最终换行处理
    for evt in events:
        evt['text'] = _wrap_long_line(evt['text'], 18)

    return events


def _apply_script_texts(timing_events: list[dict],
                        script_texts: list[str]) -> list[dict]:
    """将脚本文本按比例映射到 Whisper 时间槽，保留精确时间轴。"""
    # 拼接所有脚本句
    all_script = '\n'.join(script_texts)
    script_sentences = []
    parts = re.split(r'([，。！？])', all_script)
    i = 0
    while i < len(parts):
        s = parts[i].strip()
        p = parts[i + 1] if i + 1 < len(parts) else ''
        i += 2
        if s:
            script_sentences.append(s + p)

    n_events = len(timing_events)
    n_sents = len(script_sentences)
    if n_events == 0 or n_sents == 0:
        return timing_events

    result = []
    for j in range(n_events):
        start_idx = int(j / n_events * n_sents)
        end_idx = int((j + 1) / n_events * n_sents)
        if end_idx <= start_idx:
            end_idx = start_idx + 1
        if end_idx > n_sents:
            end_idx = n_sents
        merged = ''.join(script_sentences[start_idx:end_idx])
        result.append({
            'text': merged,
            'start': timing_events[j]['start'],
            'end': timing_events[j]['end'],
        })
    return result


def _wrap_long_line(text: str, max_chars: int = 18) -> str:
    """长字幕行自动插入 ASS 换行符 \\N。"""
    if len(text) <= max_chars:
        return text
    # 在中点附近找标点断开
    mid = len(text) // 2
    for i in range(mid, max(mid - 6, 0), -1):
        if text[i] in '，。！？、 ':
            return text[:i + 1] + r'\N' + text[i + 1:]
    # 无标点则在中点硬断
    return text[:mid] + r'\N' + text[mid:]


# ======================================================================
# 3. 语速 & 字幕工具
# ======================================================================

def speed_factor(speed: str) -> float:
    if '快' in speed:
        return 0.85
    elif '慢' in speed:
        return 1.15
    return 1.0


def _fmt_ass_time(sec: float) -> str:
    from datetime import timedelta
    td = timedelta(seconds=sec)
    total = int(td.total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    cs = int((sec - int(sec)) * 100)
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def subtitle_to_ass(text: str, start_sec: float, end_sec: float,
                    style: str = 'normal') -> str:
    styles = {
        'normal': r'{\fs55\bord2\shad1\c&HFFFFFF&\3c&H000000&}',
        'emphasis': r'{\fs80\bord3\shad1\c&H00FFFF&\3c&H000000&\an5}',
        'jinju': r'{\fs90\bord4\shad1\c&HFFFFFF&\3c&H000000&\an5}',
        'chapter': r'{\fs64\bord3\shad2\c&H00FFD0&\3c&H000000&\an8}',
    }
    st = styles.get(style, styles['normal'])
    return (f'Dialogue: 0,{_fmt_ass_time(start_sec)},{_fmt_ass_time(end_sec)},'
            f'{style},,0,0,0,,{st}{text}')


def subtitle_to_ass_move(text: str, start_sec: float, end_sec: float,
                         x1: int, y1: int, x2: int, y2: int) -> str:
    """带位移的字幕事件。"""
    st = r'{\fs55\bord2\shad1\c&HFFFFFF&\3c&H000000&\move(%d,%d,%d,%d)}' % (x1, y1, x2, y2)
    return (f'Dialogue: 0,{_fmt_ass_time(start_sec)},{_fmt_ass_time(end_sec)},'
            f'normal,,0,0,0,,{st}{text}')


def build_ass_header(width: int, height: int, font_name: str = '思源黑体') -> str:
    return f"""[Script Info]
Title: 自动生成字幕
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: normal,{font_name},55,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,100,100,60,1
Style: emphasis,{font_name},80,&H0000FFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,1,5,100,100,60,1
Style: jinju,{font_name},90,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,110,110,0,0,1,4,1,5,100,100,60,1
Style: chapter,{font_name},64,&H0000FFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,1,2,100,100,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ======================================================================
# 4. 核心流程
# ======================================================================

def generate_timeline(script_path: str, video_path: str) -> dict:
    print('=' * 60)
    print('  视频自动剪辑引擎 v2.0')
    print('=' * 60)

    print('\n[1/3] 解析脚本标注...')
    segments = parse_script(script_path)
    print(f'  共 {len(segments)} 个段落：')
    for seg in segments:
        print(f'    [{seg["section"]}] {seg["text"][:40]}... '
              f'语速={seg["speed"]} 强调={seg["emphasis"]}')

    print('\n[2/3] Whisper 转写素材...')
    words = transcribe_footage(video_path)
    full_text = ''.join(w['word'] for w in words)
    print(f'  转写字数: {len(full_text)} 字符')
    print(f'  素材时长: {words[-1]["end"]:.1f}s')

    print('\n[3/3] 对齐脚本与素材...')
    timeline = []
    for seg in segments:
        t_start, t_end = simple_text_match(seg['text'], words)
        timeline.append((t_start, t_end))
        dur = t_end - t_start
        print(f'  [{seg["section"]}] {t_start:.1f}s → {t_end:.1f}s (时长 {dur:.1f}s)')

    total = sum(max(e - s, 0) for s, e in timeline)
    print(f'\n  预估成片时长: {total:.1f}s')

    return {
        'segments': segments,
        'timeline': timeline,
        'total_duration': total,
        'source_duration': words[-1]['end'] if words else 0,
        'words': words,
    }


def transcribe_only(video_path: str) -> list[dict]:
    """只转录，返回带时间戳的段落列表。"""
    words = transcribe_footage(video_path)
    # 合并为句子级段落
    segments = []
    current_text = ''
    current_start = 0.0
    current_end = 0.0

    for w in words:
        word_text = w['word']
        if not current_text:
            current_start = w['start']
        current_text += word_text
        current_end = w['end']
        # 遇到句尾标点则断句
        if word_text and word_text[-1] in '。！？.!?\n':
            segments.append({
                'text': current_text.strip(),
                'start': current_start,
                'end': current_end,
            })
            current_text = ''

    # 剩余文字
    if current_text.strip():
        segments.append({
            'text': current_text.strip(),
            'start': current_start,
            'end': current_end,
        })

    return segments


def render_video(video_path: str, segments: list[dict],
                 timeline: list[tuple[float, float]],
                 output_path: str, bgm_path: Optional[str] = None,
                 font_path: Optional[str] = None,
                 words: Optional[list[dict]] = None) -> str:
    """逐段裁剪 + concat → 字幕烧录 → BGM 闪避混音 → 输出成片。"""
    import shutil

    if not shutil.which('ffmpeg'):
        raise RuntimeError('未找到 ffmpeg，请确认已安装并加入 PATH')

    tmpdir = tempfile.mkdtemp(prefix='video_edit_')
    clip_files = []

    # 获取原视频尺寸
    probe_cmd = [
        'ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'csv=p=0', video_path
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    try:
        w, h = result.stdout.strip().split(',')
        width, height = int(w), int(h)
    except Exception:
        width, height = 1080, 1920

    # 确定字体
    font_name = '思源黑体'
    fonts_dir = None
    if font_path and os.path.exists(font_path):
        # 复制字体到 ASCII 安全路径，与字幕同目录
        safe_font_dir = os.path.join('D:/', '_videosub')
        os.makedirs(safe_font_dir, exist_ok=True)
        safe_font_path = os.path.join(safe_font_dir, os.path.basename(font_path))
        if not os.path.exists(safe_font_path):
            shutil.copy2(font_path, safe_font_path)
        # 用 libass @前缀引用同目录字体，避免 fontsdir 路径解析问题
        font_name = '@' + os.path.splitext(os.path.basename(font_path))[0]
        font_name = font_name.replace(' ', '')

    # --- 逐段裁剪 ---
    for i, (seg, (t_start, t_end)) in enumerate(zip(segments, timeline)):
        duration = t_end - t_start
        if duration <= 0.05:
            continue

        clip_path = os.path.join(tmpdir, f'clip_{i:03d}.mp4')
        filters = []
        sf = speed_factor(seg['speed'])
        if sf != 1.0:
            filters.append(f'setpts={sf}*PTS')
        if seg.get('emphasis'):
            filters.append(
                f'scale={int(width*1.1)}:{int(height*1.1)}:force_original_aspect_ratio=increase,'
                f'crop={width}:{height}'
            )

        cmd = ['ffmpeg', '-y', '-ss', str(t_start), '-t', str(duration),
               '-i', video_path]
        if filters:
            cmd.extend(['-vf', ','.join(filters)])
        cmd.extend(['-c:v', 'libx264', '-crf', '20',
                    '-preset', 'fast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '128k'])
        cmd.append(clip_path)
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0 or not os.path.exists(clip_path):
            stderr_tail = (result.stderr or b'')[-300:].decode('utf-8', errors='replace')
            print(f'  ⚠ 片段{i} [{seg["section"]}] 裁剪失败:\n{stderr_tail}')
            continue
        clip_files.append(clip_path)

    if not clip_files:
        raise RuntimeError('没有可渲染的片段')

    # --- Concat ---
    concat_list = os.path.join(tmpdir, 'concat.txt')
    with open(concat_list, 'w', encoding='utf-8') as f:
        for cf in clip_files:
            f.write(f"file '{cf}'\n")

    concat_video = os.path.join(tmpdir, 'concat.mp4')
    result = subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_list, '-c', 'copy', concat_video,
    ], capture_output=True)
    if result.returncode != 0 or not os.path.exists(concat_video):
        stderr_tail = (result.stderr or b'')[-500:].decode('utf-8', errors='replace')
        raise RuntimeError(f'concat 失败:\n{stderr_tail}')

    # --- 字幕 ---
    # FFmpeg ASS 滤镜对含盘符/非ASCII/空格的路径敏感
    # 用 D:\_videosub\ 避免所有问题
    sub_dir = os.path.join('D:/', '_videosub')
    os.makedirs(sub_dir, exist_ok=True)
    sub_path = os.path.join(sub_dir, 'subtitle.ass')
    # 转义冒号防止被 FFmpeg 滤镜解析器当作选项分隔符
    sub_path_safe = sub_path.replace('\\', '/').replace(':', '\\:')
    with open(sub_path, 'w', encoding='utf-8') as f:
        f.write(build_ass_header(width, height, font_name))
        current_time = 0.0
        for seg_idx, (seg, (t_start, t_end)) in enumerate(zip(segments, timeline)):
            dur = t_end - t_start
            if dur <= 0:
                continue
            sf = speed_factor(seg['speed'])
            seg_out_dur = dur / sf

            # 章节标题 — 段首叠加，持续 3 秒
            if seg.get('chapter_title'):
                chapter_dur = min(3.0, seg_out_dur * 0.5)
                f.write(subtitle_to_ass(seg['chapter_title'],
                        current_time, current_time + chapter_dur, 'chapter') + '\n')

            # ★ 断句字幕 — Whisper 精确时间轴 + 脚本文本
            if words:
                seg_events = words_to_subtitles(words, [(t_start, t_end)],
                                                script_texts=[seg['text']])
                for evt in seg_events:
                    # 原始时间 → 输出时间轴映射
                    out_start = current_time + (evt['start'] - t_start) / sf
                    out_end = current_time + (evt['end'] - t_start) / sf
                    f.write(subtitle_to_ass(evt['text'], out_start, out_end, 'normal') + '\n')

            # 强调字幕
            if seg.get('subtitle_overlay'):
                overlay = seg['subtitle_overlay']
                style = 'jinju' if seg['section'] == '金句' else 'emphasis'
                overlay_start = current_time + seg_out_dur * 0.15
                overlay_end = overlay_start + min(2.5, seg_out_dur * 0.7)
                f.write(subtitle_to_ass(overlay, overlay_start, overlay_end, style) + '\n')

            current_time += seg_out_dur

    # --- BGM (带闪避) ---
    if bgm_path and os.path.exists(bgm_path):
        # 获取 concat 总时长
        dur_cmd = ['ffprobe', '-v', 'quiet', '-show_entries',
                   'format=duration', '-of', 'csv=p=0', concat_video]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace')
        total_dur = float(dur_result.stdout.strip())

        # 生成音频闪避控制文件：有人声段 BGM=20%，无声段 BGM=40%
        duck_file = os.path.join(tmpdir, 'duck.txt')
        with open(duck_file, 'w', encoding='utf-8') as f:
            f.write('0.0 0.4\n')  # 开场 40%
            current_t = 0.0
            for seg_idx2, (seg2, (t_start2, t_end2)) in enumerate(zip(segments, timeline)):
                dur2 = (t_end2 - t_start2) / speed_factor(seg2['speed'])
                if dur2 <= 0:
                    continue
                seg_start_t = current_t
                seg_end_t = current_t + dur2
                # 人声段降至 20%
                f.write(f'{seg_start_t:.2f} 0.2\n')
                f.write(f'{seg_end_t:.2f} 0.2\n')
                # 段间恢复到 40%
                if seg_idx2 < len(segments) - 1:
                    f.write(f'{seg_end_t + 0.01:.2f} 0.4\n')
                current_t = seg_end_t

        # BGM: 裁剪到视频时长 + 闪避 + 淡入淡出
        sub_path_fwd = sub_path_safe
        cmd = ['ffmpeg', '-y',
               '-i', concat_video,
               '-i', bgm_path,
               '-filter_complex',
               f'[1:a]atrim=0:{total_dur},afade=t=in:d=0.5,afade=t=out:st={total_dur-0.5}:d=0.5,'
               f'volume=0.35[bgm];'
               f'[0:a][bgm]amix=inputs=2:duration=first:weights=1 0.5[audio]',
               '-map', '0:v', '-map', '[audio]',
               '-vf', f"ass='{sub_path_fwd}'" + (f":fontsdir={fonts_dir}" if fonts_dir else ""),
               '-c:v', 'libx264', '-crf', '20', '-preset', 'fast', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '128k',
               output_path]
    else:
        sub_path_fwd = sub_path_safe
        cmd = ['ffmpeg', '-y', '-i', concat_video,
               '-vf', f"ass='{sub_path_fwd}'" + (f":fontsdir={fonts_dir}" if fonts_dir else ""),
               '-c:v', 'libx264', '-crf', '20', '-preset', 'fast', '-pix_fmt', 'yuv420p',
               '-c:a', 'copy',
               output_path]

    print('\n[渲染] 正在合成最终视频...')
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if result.returncode != 0:
        stderr_tail = (result.stderr or '')[-500:]
        print(f'  ⚠ FFmpeg 错误:\n{stderr_tail}')

    # 清理
    import shutil as _shutil
    _shutil.rmtree(tmpdir, ignore_errors=True)

    return output_path


# ======================================================================
# 5. CLI
# ======================================================================

def main():
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description='视频自动剪辑引擎 v2.0')
    parser.add_argument('script_or_footage', help='脚本路径 或（--transcribe-only时）素材路径')
    parser.add_argument('footage', nargs='?', default=None, help='口播素材视频路径')
    parser.add_argument('--output', '-o', default=None, help='输出路径（默认桌面）')
    parser.add_argument('--bgm', default=None, help='BGM 音频文件路径')
    parser.add_argument('--font', default=None, help='中文字体文件路径 (.ttf/.otf)')
    parser.add_argument('--transcribe-only', action='store_true', help='只转录素材，不渲染')
    parser.add_argument('--plan-only', action='store_true', help='只输出剪辑计划，不渲染')
    parser.add_argument('--align-only', action='store_true', help='只对齐脚本与素材')
    parser.add_argument('--dry-run', action='store_true', help='只生成指令，不渲染')

    args = parser.parse_args()

    # --- 转录模式 ---
    if args.transcribe_only:
        video_path = args.script_or_footage
        if not os.path.exists(video_path):
            print(f'错误：素材不存在 — {video_path}')
            sys.exit(1)
        print('正在转录素材...')
        segments = transcribe_only(video_path)
        print(f'\n转录完成，共 {len(segments)} 个句子：\n')
        print('| # | 时间 | 文本 |')
        print('|---|------|------|')
        for i, seg in enumerate(segments):
            print(f'| {i+1} | {seg["start"]:.1f}-{seg["end"]:.1f}s | {seg["text"]} |')
        return

    # --- 计划模式（不需要素材） ---
    if args.plan_only:
        script_path = args.script_or_footage
        if not os.path.exists(script_path):
            print(f'错误：脚本文件不存在 — {script_path}')
            sys.exit(1)
        segments = parse_script(script_path)
        print('\n## 剪辑计划\n')
        print('| 拍 | 计划时间 | 叙事动作 | 情绪 | 画面 | 人声/停顿 | 声音 | 主强调 |')
        print('|----|----------|----------|------|------|-----------|------|--------|')
        total_est = 0.0
        for seg in segments:
            char_count = len(seg['text'])
            base_dur = char_count / 4.0
            sf = speed_factor(seg['speed'])
            dur = base_dur * sf if sf != 1.0 else base_dur
            total_est += dur
            planned = seg['timecode'] or '未标注'
            performance = seg['speed']
            if seg['pause']:
                performance += f'；{seg["pause"]}'
            print(f'| {seg["section"]} | {planned} | {seg["narrative_action"] or "未标注"} | '
                  f'{seg["emotion"] or "未标注"} | {seg["visual"] or "未标注"} | '
                  f'{performance} | {seg["sound"] or "未标注"} | {seg["primary_emphasis"] or "未标注"} |')
        print(f'\n预估成片时长: {total_est:.1f}s')
        return

    # --- 正常模式需要脚本+素材 ---
    script_path = args.script_or_footage
    video_path = args.footage
    if not video_path:
        print('错误：请同时提供脚本路径和素材路径')
        sys.exit(1)
    if not os.path.exists(script_path):
        print(f'错误：脚本文件不存在 — {script_path}')
        sys.exit(1)
    if not os.path.exists(video_path):
        print(f'错误：素材文件不存在 — {video_path}')
        sys.exit(1)

    # --- 对齐模式 ---
    if args.align_only:
        result = generate_timeline(script_path, video_path)
        output = {
            'script': script_path,
            'footage': video_path,
            'segments': [
                {
                    'section': s['section'],
                    'text': s['text'][:60],
                    'speed': s['speed'],
                    'emphasis': s['emphasis'],
                    'subtitle_overlay': s['subtitle_overlay'],
                    'time_start': round(tl[0], 2),
                    'time_end': round(tl[1], 2),
                    'duration': round(tl[1] - tl[0], 2),
                }
                for s, tl in zip(result['segments'], result['timeline'])
            ],
            'total_duration': round(result['total_duration'], 1),
            'source_duration': round(result['source_duration'], 1),
        }
        print('\n' + json.dumps(output, ensure_ascii=False, indent=2))
        return

    # --- 全自动渲染 ---
    result = generate_timeline(script_path, video_path)

    if args.dry_run:
        print('\n[Dry-run] 跳过渲染。')
        return

    if args.output:
        output_path = args.output
    else:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        script_name = os.path.splitext(os.path.basename(script_path))[0]
        output_path = os.path.join(desktop, f'{script_name}-成片.mp4')

    render_video(video_path, result['segments'], result['timeline'],
                 output_path, args.bgm, args.font, result.get('words'))
    print(f'\n✅ 成片已输出到: {output_path}')


if __name__ == '__main__':
    main()
