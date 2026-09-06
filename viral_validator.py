#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爆款脚本验证器 - 基于 8 大爆款特点
"""

import json
import re


class ViralScriptValidator:
    """爆款脚本验证器"""
    
    def validate(self, script, persona_profile=None):
        """验证脚本质量 - 基于 9 大爆款特点

        Args:
            script: 要验证的脚本文本
            persona_profile: 可选的人设档案dict，用于读取违禁词/偏好句式/语气词
        """
        if persona_profile is None:
            persona_profile = {}
        issues = []
        scores = {}
        
        rhythm_beats = self._extract_rhythm_beats(script)
        target_duration_match = re.search(r'^duration_sec:\s*(\d+(?:\.\d+)?)\s*$', script, re.MULTILINE)
        target_duration = float(target_duration_match.group(1)) if target_duration_match else None

        # 只统计真正需要说出口的正文，排除 YAML、标题和制作标注。
        text = self._spoken_text(script)
        word_count = len(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', text))
        
        # 1. 黄金 3 秒钩子检查（v3.0 升级：张力度检测取代关键词匹配）
        hook_section = self._extract_section(script, '开场')
        if not hook_section and rhythm_beats:
            hook_section = rhythm_beats[0]['text']
        hook_score = 0
        if hook_section:
            # 1a. 数字张力 — 两个可比数字之间有显著差距（≥15%）或单数字+惊人词
            gap_score = 0
            numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(万|套|平|折|%|成|倍|个|年|月|天|层)', hook_section)
            if len(numbers) >= 2:
                for i in range(len(numbers)):
                    for j in range(i + 1, len(numbers)):
                        n1, u1 = float(numbers[i][0]), numbers[i][1]
                        n2, u2 = float(numbers[j][0]), numbers[j][1]
                        if u1 != u2 or n1 == 0 or n2 == 0:
                            continue
                        gap = abs(n1 - n2) / max(n1, n2)
                        if gap >= 0.15:
                            gap_score = 2
                            break
                    if gap_score:
                        break
            if gap_score == 0 and len(numbers) >= 1:
                gap_score = 1  # 有数字但无显著落差
            hook_score += gap_score

            # 1b. 反常识 — '以为/觉得' + '但/其实/实际上' 结构
            reversal = re.search(
                r'(以为|觉得|本来|原本|看上去|听起来|都说|大家都).{0,30}(但|却|其实|实际上|没想到|才发现)',
                hook_section
            )
            if reversal:
                hook_score += 2
            else:
                # 降级：至少有一个强转折词且前后各有实际内容
                if re.search(r'.{10,}(但|却|其实|实际上|然而|不过).{10,}', hook_section):
                    hook_score += 1

            # 1c. 对比冲突 — 两个同维度事物的直接对撞
            contrast_patterns = [
                r'(\S+)\s*(和|跟|比|vs\.?|VS\.?|差|不如|没有|还不如)\s*(\S+)',  # A vs B
                r'(\S+).{0,10}(却|反而|倒|倒是|却反而).{0,10}(\S+)',  # A...却...B 并列对撞
                r'(\S+).{0,5}(卖不动|抢着买|没人要|没人看|没人买).+(\S+).{0,5}(卖不动|抢着买|没人要|没人看|没人买)',  # 行为对撞
            ]
            contrast = None
            for pat in contrast_patterns:
                contrast = re.search(pat, hook_section)
                if contrast:
                    break
            if contrast:
                hook_score += 2

            # 1d. 具体排除 — 否定 + 具体对象 + 原因
            exclusion = re.search(
                r'(不推荐|不要选|别买|排除|跳过|pass|不建议).{0,25}(因为|原因是|问题在于)',
                hook_section, re.IGNORECASE
            )
            if exclusion:
                hook_score += 1

            # 将原始评分 (0-7) 映射到 0-3
            hook_score = min(hook_score, 7)
            if hook_score >= 5:
                hook_score = 3
            elif hook_score >= 3:
                hook_score = 2
            elif hook_score >= 1:
                hook_score = 1

            if hook_score <= 1:
                issues.append("钩子缺乏张力（无数字落差/反常识/对比冲突/具体排除中的任一项）")
            elif hook_score == 2:
                issues.append("钩子张力偏弱（可继续加强：补充对比数据或认知反转）")
        else:
            issues.append("缺少开场钩子")
        scores['黄金3秒钩子'] = hook_score
        
        # 2. 反差感检查
        contrast_score = 0
        contrast_keywords = ['但', '却', '结果', '没想到', '出乎意料', '相反',
                           '不是...而是', '以为', '其实', '然而', '不过']
        contrast_count = sum(1 for kw in contrast_keywords if kw in text)
        if contrast_count >= 2:
            contrast_score = 3
        elif contrast_count >= 1:
            contrast_score = 2
        else:
            issues.append("缺少反差感（预期vs现实的对比）")
        scores['反差感'] = contrast_score
        
        # 3. 情感共鸣检查
        emotion_score = 0
        emotion_keywords = ['太太', '老公', '夫妻', '家人', '纠结', '犹豫',
                          '痛苦', '焦虑', '担心', '后悔', '庆幸', '终于']
        emotion_count = sum(1 for kw in emotion_keywords if kw in text)
        if emotion_count >= 2:
            emotion_score = 3
        elif emotion_count >= 1:
            emotion_score = 2
        else:
            issues.append("缺少情感共鸣（缺少人物情绪/家庭元素）")
        scores['情感共鸣'] = emotion_score
        
        # 4. 价值输出检查
        value_score = 0
        value_keywords = ['建议', '方法', '技巧', '经验', '教训', '三点',
                        '第一', '第二', '第三', '清单', '对比', '分析']
        value_count = sum(1 for kw in value_keywords if kw in text)
        if value_count >= 3:
            value_score = 3
        elif value_count >= 2:
            value_score = 2
        elif value_count >= 1:
            value_score = 1
        else:
            issues.append("缺少价值输出（缺少具体建议/方法）")
        scores['价值输出'] = value_score
        
        # 5. 真实感检查
        authenticity_score = 0
        has_client_name = bool(re.search(r'[张王李赵刘陈杨黄周吴].*微信', text))
        has_budget = bool(re.search(r'\d+万', text))
        has_community = bool(re.search(r'花园|新城|苑|小区|府|湾', text))
        has_data = bool(re.search(r'\d+套|\d+平|\d+%', text))
        
        if has_client_name:
            authenticity_score += 2
        if has_budget:
            authenticity_score += 1
        if has_community:
            authenticity_score += 1
        if has_data:
            authenticity_score += 1

        authenticity_score = min(authenticity_score, 3)
        
        if authenticity_score < 3:
            issues.append("缺少真实感（缺少具体客户名/数据/小区）")
        scores['真实感'] = authenticity_score
        
        # 6. 四维节奏检查：时间连续性、标注完整度、变化量各 1 分。
        pacing_score, pacing_issues = self._score_rhythm(rhythm_beats, target_duration)
        issues.extend(pacing_issues)
        scores['节奏感'] = pacing_score
        
        # 7. 互动钩子检查
        interaction_score = 0
        has_cta_text = '私信' in text or '关注' in text or '点赞' in text or '评论' in text
        has_question = '?' in text or '？' in text
        
        if has_cta_text:
            interaction_score += 2
        if has_question:
            interaction_score += 1
        
        if interaction_score < 2:
            issues.append("缺少互动钩子（缺少引导关注/评论/私信）")
        scores['互动钩子'] = interaction_score
        
        # 8. 人设一致性检查（升级为多维评估）
        persona_score = 0

        # 8a. 违禁词检测（0-1分） — 出现一个即扣分
        forbidden = persona_profile.get("forbidden_words", [
            '家人们', '绝绝子', '震惊', '重磅', '赶紧', '手慢无', '错过不再',
            '必看', '血赚', '抢疯了', '暴涨', '暴跌', '千万别买', '不要错过'
        ])
        found_forbidden = [w for w in forbidden if w in script]
        if not found_forbidden:
            persona_score += 1
        else:
            issues.append(f"人设违禁词：{', '.join(found_forbidden[:3])}")

        # 8b. 偏好句式检测（0-1分） — 匹配朋友聊天式表达
        preferred = persona_profile.get("preferred_patterns", [
            '我建议', '我觉得', '你可以考虑', '不妨看看', '说实话', '说白了',
            '帮你分析', '帮你看清', '搞清楚', '说真的'
        ])
        found_preferred = [p for p in preferred if p in text]
        if len(found_preferred) >= 2:
            persona_score += 1
        elif len(found_preferred) >= 1:
            persona_score += 0  # 有但不加分也不扣分
        else:
            issues.append("人设语言缺失：缺少'我建议/说实话/帮你分析'等柔和表达")

        # 8c. 催促/恐吓/夸大语气检测（0-1分）
        avoid_expr = persona_profile.get("avoid_expressions", {})
        aggressive_tones = (
            avoid_expr.get("urgency", [])
            + avoid_expr.get("hype", [])
            + avoid_expr.get("fear_mongering", [])
        )
        if not aggressive_tones:
            aggressive_tones = ['赶紧下手', '手慢就没了', '再不下手', '再不上车',
                               '以后就买不起了', '爆款', '神盘', '天花板', '顶级']
        found_aggressive = [t for t in aggressive_tones if t in text]
        if not found_aggressive:
            persona_score += 1
        else:
            issues.append(f"语气不当：{', '.join(found_aggressive[:3])}")

        scores['人设一致性'] = persona_score
        
        # 9. 中心思想明确性（新增）
        thesis_score = 0
        # 检查是否有核心观点信号词
        thesis_signals = ['其实就', '说白了', '关键不在于', '真正的问题是', '说到底',
                         '很多人以为', '实际上', '真正原因', '核心逻辑']
        found_thesis = [s for s in thesis_signals if s in text]
        if len(found_thesis) >= 2:
            thesis_score = 3
        elif len(found_thesis) >= 1:
            thesis_score = 2
        else:
            issues.append("中心思想不够明确（缺少'其实/说白了/说到底'等核心观点表达）")
            thesis_score = 1
        scores['中心思想明确性'] = thesis_score

        # 长度验证：有目标时长时按口播密度检查；旧脚本沿用宽松范围。
        if target_duration:
            min_chars = round(target_duration * 3.0)
            max_chars = round(target_duration * 5.0)
            if word_count < min_chars:
                issues.append(f"口播密度偏低（{word_count}字/{target_duration:.0f}秒，建议约{min_chars}-{max_chars}字并以试读为准）")
            elif word_count > max_chars:
                issues.append(f"口播密度偏高（{word_count}字/{target_duration:.0f}秒，建议约{min_chars}-{max_chars}字并优先删旁支）")
        elif word_count < 250:
            issues.append(f"脚本太短（{word_count}字；建议先填写 duration_sec，再按时长检查）")
        elif word_count > 600:
            issues.append(f"脚本太长（{word_count}字；建议先填写 duration_sec，再按时长检查）")
        
        # 内部术语验证
        if "【" in script or "】" in script:
            issues.append("包含内部术语（【】）")
        
        # 计算总分
        total_score = sum(scores.values())
        max_score = 27  # 9 项 * 3 分
        
        return {
            "valid": len(issues) == 0,
            "word_count": word_count,
            "issues": issues,
            "scores": scores,
            "total_score": total_score,
            "max_score": max_score,
            "pass_rate": total_score / max_score if max_score > 0 else 0
        }
    
    def _extract_section(self, script, section_name):
        """提取脚本中的某个段落"""
        pattern = rf'## {section_name}[^\n]*\n([^#]*?)(?=## |---|$)'
        match = re.search(pattern, script, re.DOTALL)
        return match.group(1).strip() if match else None

    def _spoken_text(self, script):
        """移除 YAML、标题和方括号制作标注，只保留口播正文。"""
        text = script
        if text.startswith('---'):
            parts = text.split('---', 2)
            text = parts[2] if len(parts) > 2 else text
        text = re.sub(r'^#{1,6}.*$', '', text, flags=re.MULTILINE)
        labels = (
            '时间', '叙事动作', '情绪', '画面', '字幕叠加', '语速',
            '停顿', '声音', '转场理由', '主强调', '章节标题'
        )
        for label in labels:
            text = re.sub(rf'\[{label}:[^\]]*\]', '', text)
        return text.strip()

    def _extract_rhythm_beats(self, script):
        """提取 `## 节奏拍` 区块和四维标注。"""
        beats = []
        blocks = re.split(r'(?m)^##\s+', script)
        labels = ('时间', '叙事动作', '情绪', '画面', '语速', '停顿', '声音', '转场理由', '主强调')
        for block in blocks:
            lines = block.strip().splitlines()
            if not lines or not lines[0].startswith('节奏拍'):
                continue
            body = '\n'.join(lines[1:])
            annotations = {}
            for label in labels:
                match = re.search(rf'\[{label}:\s*([^\]]+)\]', body)
                annotations[label] = match.group(1).strip() if match else None
            beat_text = body
            for label in labels + ('字幕叠加', '章节标题'):
                beat_text = re.sub(rf'\[{label}:[^\]]*\]', '', beat_text)
            beats.append({
                'title': lines[0].strip(),
                'annotations': annotations,
                'time': self._parse_time_range(annotations['时间']),
                'text': beat_text.strip(),
            })
        return beats

    def _parse_time_range(self, value):
        if not value:
            return None
        match = re.fullmatch(
            r'\s*(\d{1,2}):(\d{2}(?:\.\d+)?)\s*[-–—]\s*(\d{1,2}):(\d{2}(?:\.\d+)?)\s*',
            value,
        )
        if not match:
            return None
        start = int(match.group(1)) * 60 + float(match.group(2))
        end = int(match.group(3)) * 60 + float(match.group(4))
        return start, end

    def _score_rhythm(self, beats, target_duration):
        """按可拍时间轴评分，而不是按章节标题评分。"""
        if not beats:
            return 0, ["节奏感不足（缺少 `## 节奏拍` 时间轴，章节齐全不等于节奏成立）"]

        score = 0
        rhythm_issues = []
        if target_duration is None:
            rhythm_issues.append("缺少 duration_sec，无法核对节奏拍是否覆盖目标时长")

        if target_duration is not None and target_duration <= 45:
            min_beats = 4
        elif target_duration is not None and target_duration <= 75:
            min_beats = 6
        elif target_duration is not None:
            min_beats = 8
        else:
            min_beats = 6

        parsed_times = [beat['time'] for beat in beats]
        timing_ok = len(beats) >= min_beats and all(parsed_times)
        if timing_ok:
            if parsed_times[0][0] > 0.1:
                timing_ok = False
            for previous, current in zip(parsed_times, parsed_times[1:]):
                gap = current[0] - previous[1]
                if gap < -0.1 or gap > 1.0:
                    timing_ok = False
                    break
            if any(end <= start or end - start > 12.0 for start, end in parsed_times):
                timing_ok = False
            if target_duration is not None and abs(parsed_times[-1][1] - target_duration) > 1.0:
                timing_ok = False
        if timing_ok:
            score += 1
        else:
            rhythm_issues.append(
                f"节奏时间轴不合格（当前{len(beats)}拍，需至少{min_beats}拍；检查时间码连续性、单拍≤12秒和目标时长覆盖）"
            )

        required = ('叙事动作', '情绪', '画面', '语速', '停顿', '声音', '转场理由', '主强调')
        filled = sum(bool(beat['annotations'][label]) for beat in beats for label in required)
        completeness = filled / (len(beats) * len(required))
        emphasis_values = [beat['annotations']['主强调'] for beat in beats if beat['annotations']['主强调']]
        emphasis_ok = all(value in {'台词', '画面', '字幕', '声音'} for value in emphasis_values)
        if completeness >= 0.9 and emphasis_ok:
            score += 1
        else:
            rhythm_issues.append(
                f"四维节奏标注不完整或主强调无效（完整度{completeness:.0%}；主强调只能是台词/画面/字幕/声音）"
            )

        emotions = [beat['annotations']['情绪'] for beat in beats if beat['annotations']['情绪']]
        visuals = [beat['annotations']['画面'] for beat in beats if beat['annotations']['画面']]
        sounds = [beat['annotations']['声音'] for beat in beats if beat['annotations']['声音']]
        speeds = [beat['annotations']['语速'] for beat in beats if beat['annotations']['语速']]
        emotion_levels = [
            tuple(int(n) for n in re.findall(r'[1-5]', emotion))
            for emotion in emotions
        ]
        deltas = [levels[-1] - levels[0] for levels in emotion_levels if len(levels) >= 2]
        has_emotion_curve = any(delta > 0 for delta in deltas) and any(delta < 0 for delta in deltas)
        has_channel_variation = (
            len(set(visuals)) >= 3
            and len(set(sounds)) >= 3
            and (len(set(speeds)) >= 2 or any('→' in speed for speed in speeds))
        )
        if has_emotion_curve and has_channel_variation:
            score += 1
        else:
            rhythm_issues.append("节奏变化不足（需同时具备情绪上升与回落、至少3种画面状态、3种声音状态和2种表演速度）")

        return score, rhythm_issues
    
    def print_report(self, validation):
        """打印验证报告"""
        print("\n" + "="*50)
        print("[SCRIPT] 爆款脚本验证报告")
        print("="*50)
        
        # 总分
        total = validation['total_score']
        max_score = validation['max_score']
        rate = validation['pass_rate']
        
        print(f"\n总分：{total}/{max_score} ({rate*100:.0f}%)")
        
        if rate >= 0.8:
            print("[PASS] 优质脚本（80% 以上）")
        elif rate >= 0.6:
            print("[WARN] 合格脚本（60-80%）")
        else:
            print("[FAIL] 不合格脚本（60% 以下）")
        
        # 各项得分
        print("\n[SCORES] 各项得分：")
        for name, score in validation['scores'].items():
            bar = '#' * score + '-' * (3 - score)
            print(f"  {name:10s} [{bar}] {score}/3")
        
        # 问题列表
        if validation['issues']:
            print(f"\n[WARN] 发现问题（{len(validation['issues'])}项）：")
            for i, issue in enumerate(validation['issues'], 1):
                print(f"  {i}. {issue}")
        else:
            print("\n[PASS] 无问题")
        
        print("="*50 + "\n")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    validator = ViralScriptValidator()

    if len(sys.argv) > 1:
        # CLI mode: python viral_validator.py <script_file>
        script = Path(sys.argv[1]).read_text(encoding="utf-8")
        persona_path = Path("config/persona.json")
        persona = json.loads(persona_path.read_text(encoding="utf-8")) if persona_path.exists() else {}
        result = validator.validate(script, persona)
        validator.print_report(result)
    else:
        # Test mode
        validator = ViralScriptValidator()

        test_script = """---
duration_sec: 60
---
# 1500万买房，夫妻意见不统一怎么办？

## 节奏拍 01｜冲突
[时间: 00:00-00:08] [叙事动作: 抛出预算与选择冲突] [情绪: 平静1→疑惑3]
[画面: 夫妻看房背影] [语速: 快→正常] [停顿: 问句后0.4秒] [声音: 裸声] [转场理由: 用真实经历回答原因] [主强调: 台词]
同样1500万预算，一套房太太坚决不要，老公却觉得错过可惜。问题到底出在哪？

## 节奏拍 02｜人物
[时间: 00:08-00:18] [叙事动作: 建立真实人物与分歧] [情绪: 疑惑3→焦虑4]
[画面: 小区大门→人物中景] [语速: 正常] [停顿: 转折前0.5秒] [声音: BGM淡入] [转场理由: 从争执推进到选择标准] [主强调: 画面]
张先生微信约我看长宁新城，夫妻俩本来觉得预算够了，结果太太担心采光，老公只盯着低了8%的价格。

## 节奏拍 03｜清单
[时间: 00:18-00:28] [叙事动作: 给出第一步方法] [情绪: 焦虑4→理解3]
[画面: 清单卡片] [语速: 稍快] [停顿: 列举间0.2秒] [声音: BGM稳定] [转场理由: 从方法转入隐藏代价] [主强调: 字幕]
我建议先别争，我帮他们列三张清单：必须满足、可以妥协、绝对不能接受。这样讨论的就不是输赢，而是生活。

## 节奏拍 04｜反转
[时间: 00:28-00:38] [叙事动作: 揭示低价背后的代价] [情绪: 放心2→警觉5]
[画面: 窗景→户型图] [语速: 正常→慢] [停顿: “但”前0.6秒] [声音: BGM抽低] [转场理由: 用具体数据改变低价判断] [主强调: 台词]
但真正的问题不是采光。那套房便宜120万，却要牺牲每天的通勤和孩子的活动空间，这才是长期成本。

## 节奏拍 05｜判断
[时间: 00:38-00:50] [叙事动作: 完成认知反转并给判断] [情绪: 警觉5→确信4]
[画面: 人物稳定近景] [语速: 慢→正常] [停顿: 核心句后0.9秒] [声音: 静音0.5秒→BGM恢复] [转场理由: 把判断落到可执行选择] [主强调: 台词]
说白了，夫妻买房不是把两个人的偏好相加，而是先找出谁承担哪个代价。搞清楚这一点，选择反而简单。

## 节奏拍 06｜收束
[时间: 00:50-01:00] [叙事动作: 落地方法并发出经历型提问] [情绪: 确信4→回味2]
[画面: 小区远景] [语速: 正常] [停顿: 问句后1秒] [声音: BGM淡出] [转场理由: 用观众经历完成互动] [主强调: 台词]
第一看共同底线，第二算长期代价，第三再比较价格。你看房时也遇到过家人意见不一致吗？关注我，我继续帮你分析。"""

        result = validator.validate(test_script)
        validator.print_report(result)
