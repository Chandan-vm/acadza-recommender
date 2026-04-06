"""
Buggy Recommendation Engine - debug/recommender_buggy.py
Find the bug, fix it, explain what went wrong.
"""

import json
from collections import defaultdict


def load_data(performance_path, question_bank_path):
    with open(performance_path) as f:
        students = json.load(f)
    with open(question_bank_path) as f:
        questions = json.load(f)
    return students, questions


def parse_marks(marks_str):
    """Parse various marks formats to a percentage score."""
    marks_str = str(marks_str).strip()

    # Format: "+52 -8"
    if marks_str.startswith('+'):
        parts = marks_str.split()
        positive = int(parts[0].replace('+', ''))
        negative = int(parts[1].replace('-', '')) if len(parts) > 1 else 0
        total = positive - negative
        # Assume max is positive total if no denominator
        return (total / (positive + negative)) * 100 if (positive + negative) > 0 else 0

    # Format: "68/100" or "34/75 (45.3%)"
    if '/' in marks_str:
        fraction = marks_str.split('(')[0].strip()
        num, denom = fraction.split('/')
        return (float(num.strip()) / float(denom.strip())) * 100

    # Format: bare number like "72" or "28"
    try:
        val = float(marks_str)
        # BUG: assumes bare number IS a percentage — but it's actually raw marks
        # For a 100-mark paper, "72" means 72% — so this seems fine... right?
        return val  # <--- THE BUG: returns raw marks as if it's percentage for all bare numbers
    except ValueError:
        return 0.0


def compute_student_profile(student):
    """Compute per-chapter strength/weakness profile for a student."""
    chapter_scores = defaultdict(list)
    chapter_attempts = defaultdict(int)
    chapter_skips = defaultdict(int)

    for session in student['sessions']:
        score_pct = parse_marks(session['marks'])
        chapters = session['chapters']
        attempted = session['attempted']
        skipped = session['skipped']
        total = session['total_questions']

        for chapter in chapters:
            chapter_scores[chapter].append(score_pct)
            chapter_attempts[chapter] += attempted
            chapter_skips[chapter] += skipped

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
    # Sort weakest first
    weak.sort(key=lambda x: x[1])
    return weak


def recommend_dost(student, profile, dost_config, question_bank):
    """
    Generate DOST recommendations for a student.
    Returns a list of recommendation steps.
    """
    weak_chapters = get_weak_chapters(profile)
    recommendations = []
    step = 1

    # Get questions indexed by topic
    questions_by_topic = defaultdict(list)
    for q in question_bank:
        questions_by_topic[q.get('topic', '')].append(q)

    recent_sessions = student['sessions'][-3:]
    avg_time = sum(s['avg_time_per_question_seconds'] for s in recent_sessions) / len(recent_sessions)
    completion_rate = sum(1 for s in student['sessions'] if s['completed']) / len(student['sessions'])

    for chapter, score in weak_chapters[:3]:
        # Decide DOST type based on score band
        if score < 35:
            dost_type = 'concept'
            message = f"Your score in {chapter} is critically low ({score:.1f}%). Start with concept building."
        elif score < 55:
            dost_type = 'practiceAssignment'
            message = f"Your score in {chapter} is {score:.1f}%. Practice targeted problems to improve."
        else:
            dost_type = 'practiceTest'
            message = f"Your score in {chapter} is {score:.1f}%. Take a focused test to strengthen this."

        # Get relevant question IDs
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

    # Add speed drill if student is slow
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

    # Add revision if completion rate is low
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


if __name__ == "__main__":
    result = run_recommender(
        "data/student_performance.json",
        "data/question_bank.json",
        "data/dost_config.json",
        "STU_001"
    )
    print(json.dumps(result, indent=2))
