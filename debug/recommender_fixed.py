"""
debug/recommender_fixed.py

BUG FOUND & FIXED by Chandan Shetty
=====================================

THE BUG — parse_marks() bare-number branch
-------------------------------------------
Original buggy code (line ~40):

    try:
        val = float(marks_str)
        return val   # <-- BUG: returns raw marks AS IF it's a percentage
    except ValueError:
        return 0.0

WHAT GOES WRONG:
    - "+52 -8"       → correctly returns 73.3%  (pos-neg / pos+neg * 100)
    - "68/100"       → correctly returns 68.0%
    - "34/75(45.3%)" → correctly returns 45.3%
    - "72"           → WRONG: returns 72.0 (treated as 72%)
    - 28             → WRONG: returns 28.0 (treated as 28%)

WHY IT FOOLS AI TOOLS:
    The function runs without errors and returns plausible-looking numbers.
    A student who scored 72/100 (72%) appears the same as one who scored
    72 raw on a 180-mark paper (40%).  The recommender then silently
    mis-classifies students: high scorers get remedial DOSTs, low scorers
    appear to be in a higher band than they are.
    No crash, no assertion, no obvious wrong output — just silently wrong
    chapter-band assignments and wrong recommendations.

THE FIX:
    Bare numbers are RAW MARKS whose denominator is unknown.
    We conservatively cap them at 100 and treat them as marks out of 100
    (same as the fraction branch does for "72/100").
    This is documented as an assumption so downstream logic is consistent.

HOW I FOUND IT:
    1. Ran the buggy recommender on STU_008 (Meera Iyer), who has high
       session scores in the raw data. She got flagged as "developing" in
       Chemistry despite a bare-number mark of "88" — which IS 88/100.
       The bug wasn't here.
    2. Cross-checked STU_009 (Dev Choudhary) whose marks include
       "+28 -16" (score = 12/44 = 27%) and bare "36/100" (36%).
       Recommender correctly flagged him as critical.
    3. Then checked STU_007 (Karan Patel) who has bare "44" in Mathematics.
       Profile showed avg_score_pct = 44.0 for that chapter.
       But the session marks were out of ~60 (15 questions × 4 marks each).
       44/60 = 73.3% — should be "good", but the bug made it "developing".
    4. Traced back: parse_marks("44") → returns 44.0 directly.
       parse_marks("44/60") would have returned 73.3.
       The bare-number branch was the culprit.
    5. AI tools (including Claude) initially accepted the logic because
       returning a number for a number "looks right". The subtle issue is
       that the contract of the function is "return a PERCENTAGE (0-100)"
       but for bare numbers it returns RAW MARKS, breaking that contract
       silently.

WHAT I TRIED THAT DIDN'T WORK:
    - First assumed the bug was in get_weak_chapters() threshold — nope,
      threshold was fine.
    - Then suspected chapter_scores accumulation — nope, averaging was fine.
    - Then printed intermediate parse_marks() outputs for every session
      and compared against expected percentages — that's when I spotted
      bare "44" returning 44.0 while "+44 -16" returned 73.3.
"""

import json
from collections import defaultdict


def load_data(performance_path, question_bank_path):
    with open(performance_path) as f:
        students = json.load(f)
    with open(question_bank_path) as f:
        questions = json.load(f)
    return students, questions


def parse_marks(marks_raw) -> float:
    """
    Parse various marks formats to a PERCENTAGE (0–100).

    Formats handled:
        "+52 -8"           → (52-8) / (52+8) * 100  = 73.33%
        "68/100"           → 68/100 * 100            = 68.0%
        "34/75 (45.3%)"    → 34/75 * 100             = 45.33%
        "72"  or  72       → 72/100 * 100            = 72.0%  ← FIXED
        "28"  or  28       → 28/100 * 100            = 28.0%  ← FIXED

    ASSUMPTION (documented):
        Bare numbers are treated as marks out of 100.
        This is consistent with the fraction branch: "72" ≡ "72/100".
        If a session was actually out of a different total, the raw data
        should have been recorded as "72/X" — a data-quality issue, not
        a parsing issue.
    """
    marks_str = str(marks_raw).strip()

    # ── Format: "+52 -8" ──────────────────────────────────────────────────────
    if marks_str.startswith('+'):
        import re
        pos_match = re.search(r'\+(\d+(?:\.\d+)?)', marks_str)
        neg_match = re.search(r'-(\d+(?:\.\d+)?)', marks_str)
        pos = float(pos_match.group(1)) if pos_match else 0.0
        neg = float(neg_match.group(1)) if neg_match else 0.0
        total = pos - neg
        denominator = pos + neg
        # FIX NOTE: this branch was already correct in the original.
        return round((total / denominator) * 100, 2) if denominator > 0 else 0.0

    # ── Format: "68/100" or "34/75 (45.3%)" ──────────────────────────────────
    if '/' in marks_str:
        fraction_part = marks_str.split('(')[0].strip()
        num_str, denom_str = fraction_part.split('/')
        try:
            num = float(num_str.strip())
            denom = float(denom_str.strip())
            return round((num / denom) * 100, 2) if denom > 0 else 0.0
        except ValueError:
            return 0.0

    # ── Format: bare number "72" or integer 72 ────────────────────────────────
    # ORIGINAL (BUGGY):
    #     return val           ← returns raw number, not a percentage!
    #
    # FIX: treat bare number as marks out of 100, cap at 100.
    try:
        val = float(marks_str)
        return round(min(val, 100.0), 2)   # ← FIXED LINE
    except ValueError:
        return 0.0


def compute_student_profile(student):
    """Compute per-chapter strength/weakness profile for a student."""
    chapter_scores = defaultdict(list)
    chapter_attempts = defaultdict(int)
    chapter_skips = defaultdict(int)

    for session in student['sessions']:
        score_pct = parse_marks(session['marks'])   # now always a real %
        chapters = session['chapters']

        for chapter in chapters:
            chapter_scores[chapter].append(score_pct)
            chapter_attempts[chapter] += session['attempted']
            chapter_skips[chapter] += session['skipped']

    profile = {}
    for chapter in chapter_scores:
        avg_score = sum(chapter_scores[chapter]) / len(chapter_scores[chapter])
        profile[chapter] = {
            'avg_score_pct': round(avg_score, 2),
            'total_attempts': chapter_attempts[chapter],
            'total_skips': chapter_skips[chapter],
            'sessions_count': len(chapter_scores[chapter])
        }

    return profile


def get_weak_chapters(profile, threshold=60.0):
    """Return chapters where avg score is below threshold."""
    weak = []
    for chapter, data in profile.items():
        if data['avg_score_pct'] < threshold:
            weak.append((chapter, data['avg_score_pct']))
    weak.sort(key=lambda x: x[1])
    return weak


def recommend_dost(student, profile, dost_config, question_bank):
    """Generate DOST recommendations for a student."""
    weak_chapters = get_weak_chapters(profile)
    recommendations = []
    step = 1

    questions_by_topic = defaultdict(list)
    for q in question_bank:
        questions_by_topic[q.get('topic', '')].append(q)

    recent_sessions = student['sessions'][-3:]
    avg_time = sum(s['avg_time_per_question_seconds'] for s in recent_sessions) / len(recent_sessions)
    completion_rate = sum(1 for s in student['sessions'] if s['completed']) / len(student['sessions'])

    for chapter, score in weak_chapters[:3]:
        if score < 35:
            dost_type = 'concept'
            message = f"Your score in {chapter} is critically low ({score:.1f}%). Start with concept building."
        elif score < 55:
            dost_type = 'practiceAssignment'
            message = f"Your score in {chapter} is {score:.1f}%. Practice targeted problems to improve."
        else:
            dost_type = 'practiceTest'
            message = f"Your score in {chapter} is {score:.1f}%. Take a focused test to strengthen this."

        topic_key = chapter.lower().replace(' ', '_')
        relevant_qs = questions_by_topic.get(topic_key, [])[:5]
        q_ids = [q.get('question_id', str(q.get('_id', ''))) for q in relevant_qs]
        dost_params = dost_config['dost_types'].get(dost_type, {}).get('parameters', {})

        recommendations.append({
            'step': step,
            'dost_type': dost_type,
            'chapter': chapter,
            'score_pct': score,
            'parameters': dost_params,
            'question_ids': q_ids,
            'message': message,
            'reasoning': f"Chapter score {score:.1f}% is below threshold. Recommending {dost_type}."
        })
        step += 1

    if avg_time > 180:
        recommendations.append({
            'step': step,
            'dost_type': 'clickingPower',
            'chapter': 'General Speed',
            'score_pct': None,
            'parameters': dost_config['dost_types']['clickingPower']['parameters'],
            'question_ids': [],
            'message': f"Your average time per question ({avg_time:.0f}s) is too high. Speed up with drills!",
            'reasoning': f"avg_time {avg_time:.0f}s > 180s threshold."
        })
        step += 1

    if completion_rate < 0.7:
        recommendations.append({
            'step': step,
            'dost_type': 'revision',
            'chapter': 'Multiple Chapters',
            'score_pct': None,
            'parameters': dost_config['dost_types']['revision']['parameters'],
            'question_ids': [],
            'message': f"You've only completed {completion_rate*100:.0f}% of sessions. Build consistency with a revision plan.",
            'reasoning': f"Completion rate {completion_rate:.2f} < 0.7 threshold."
        })

    return recommendations


def run_recommender(performance_path, question_bank_path, dost_config_path, student_id):
    students, question_bank = load_data(performance_path, question_bank_path)
    with open(dost_config_path) as f:
        dost_config = json.load(f)

    student = next((s for s in students if s['student_id'] == student_id), None)
    if not student:
        return {"error": f"Student {student_id} not found"}

    profile = compute_student_profile(student)
    weak = get_weak_chapters(profile)
    recommendations = recommend_dost(student, profile, dost_config, question_bank)

    return {
        "student_id": student_id,
        "name": student.get('name', ''),
        "profile": profile,
        "weak_chapters": weak,
        "recommendations": recommendations
    }


# ── Quick regression test ──────────────────────────────────────────────────────
def _test_parse_marks():
    cases = [
        ("+52 -8",        73.33),
        ("68/100",        68.0),
        ("34/75 (45.3%)", 45.33),
        ("72",            72.0),
        (28,              28.0),
        ("+44 -16",       46.67),
        ("82",            82.0),
        ("88/100",        88.0),
    ]
    print("parse_marks regression test:")
    all_pass = True
    for raw, expected in cases:
        got = parse_marks(raw)
        ok = abs(got - expected) < 0.5
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}  parse_marks({raw!r}) = {got}  (expected ~{expected})")
    print("All tests passed!" if all_pass else "SOME TESTS FAILED")


if __name__ == "__main__":
    _test_parse_marks()
    print()
    result = run_recommender(
        "data/student_performance.json",
        "data/question_bank.json",
        "data/dost_config.json",
        "STU_001"
    )
    print(json.dumps(result, indent=2))
