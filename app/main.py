"""
Acadza AI Intern Assignment — FastAPI Recommender System
Author: Chandan Shetty
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import json
import re
import os
from collections import defaultdict
from pathlib import Path

app = FastAPI(
    title="Acadza Student Recommender API",
    description="Recommender system that analyzes student performance and recommends what to study next",
    version="1.0.0"
)

# ─── Data Paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

STUDENT_DATA_PATH = DATA_DIR / "student_performance.json"
QUESTION_BANK_PATH = DATA_DIR / "question_bank.json"
DOST_CONFIG_PATH = DATA_DIR / "dost_config.json"


# ─── Data Loaders ──────────────────────────────────────────────────────────────
def load_students():
    with open(STUDENT_DATA_PATH) as f:
        return json.load(f)

def load_question_bank():
    with open(QUESTION_BANK_PATH) as f:
        return json.load(f)

def load_dost_config():
    with open(DOST_CONFIG_PATH) as f:
        return json.load(f)


# ─── Utility: Normalize _id ────────────────────────────────────────────────────
def normalize_id(raw_id) -> str:
    """Handle both {'$oid': '...'} and flat string formats."""
    if isinstance(raw_id, dict):
        return raw_id.get("$oid", str(raw_id))
    return str(raw_id)


# ─── Utility: Parse Marks ──────────────────────────────────────────────────────
def parse_marks(marks_raw) -> dict:
    """
    Normalize all marks formats to a dict with keys:
        score (float): actual marks obtained
        max_marks (float): maximum possible marks
        pct (float): percentage score (0–100)
        positive (float): positive marks
        negative (float): negative marks (as positive number)
    
    Handles:
        "+52 -8"       → score=44, positive=52, negative=8
        "68/100"       → score=68, max=100
        "34/75 (45.3%)"→ score=34, max=75
        "72"           → score=72, assumed out of 100
        72             → same as "72"
    """
    marks_str = str(marks_raw).strip()
    result = {"raw": marks_str, "score": 0.0, "max_marks": 100.0,
              "pct": 0.0, "positive": 0.0, "negative": 0.0}

    # Format: "+52 -8" or "+52 -8 ..."
    if marks_str.startswith('+'):
        pos_match = re.search(r'\+(\d+(?:\.\d+)?)', marks_str)
        neg_match = re.search(r'-(\d+(?:\.\d+)?)', marks_str)
        pos = float(pos_match.group(1)) if pos_match else 0.0
        neg = float(neg_match.group(1)) if neg_match else 0.0
        score = pos - neg
        # Max marks = positive marks if no denominator
        max_m = pos + neg if (pos + neg) > 0 else 100.0
        result.update({"score": score, "max_marks": max_m,
                        "pct": round((score / max_m) * 100, 2),
                        "positive": pos, "negative": neg})
        return result

    # Format: "68/100" or "34/75 (45.3%)"
    if '/' in marks_str:
        fraction_part = marks_str.split('(')[0].strip()
        num_str, denom_str = fraction_part.split('/')
        try:
            num = float(num_str.strip())
            denom = float(denom_str.strip())
            result.update({"score": num, "max_marks": denom,
                            "pct": round((num / denom) * 100, 2) if denom > 0 else 0.0})
        except ValueError:
            pass
        return result

    # Bare number: "72", "28", or integer 72
    try:
        val = float(marks_str)
        # Treat as raw marks out of 100
        result.update({"score": val, "max_marks": 100.0,
                        "pct": round(min(val, 100.0), 2)})
    except ValueError:
        pass
    return result


# ─── Utility: Build Student Profile ───────────────────────────────────────────
def build_profile(student: dict) -> dict:
    """Compute chapter-wise stats, subject stats, and session trends."""
    sessions = student.get("sessions", [])
    chapter_data = defaultdict(lambda: {"scores": [], "attempts": 0, "skips": 0,
                                         "sessions": 0, "avg_time": []})
    subject_data = defaultdict(lambda: {"scores": [], "sessions": 0})
    speed_data = []
    completion_list = []
    mode_scores = {"test": [], "assignment": []}

    for sess in sessions:
        parsed = parse_marks(sess.get("marks", "0"))
        pct = parsed["pct"]
        chapters = sess.get("chapters", [])
        subject = sess.get("subject", "Unknown")
        avg_t = sess.get("avg_time_per_question_seconds", 0)
        completed = sess.get("completed", True)

        for ch in chapters:
            chapter_data[ch]["scores"].append(pct)
            chapter_data[ch]["attempts"] += sess.get("attempted", 0)
            chapter_data[ch]["skips"] += sess.get("skipped", 0)
            chapter_data[ch]["sessions"] += 1
            chapter_data[ch]["avg_time"].append(avg_t)

        subject_data[subject]["scores"].append(pct)
        subject_data[subject]["sessions"] += 1
        speed_data.append(avg_t)
        completion_list.append(1 if completed else 0)
        mode_scores[sess.get("mode", "test")].append(pct)

    # Chapter summary
    chapter_summary = {}
    for ch, d in chapter_data.items():
        avg = round(sum(d["scores"]) / len(d["scores"]), 2) if d["scores"] else 0
        avg_t = round(sum(d["avg_time"]) / len(d["avg_time"]), 1) if d["avg_time"] else 0
        chapter_summary[ch] = {
            "avg_score_pct": avg,
            "total_attempts": d["attempts"],
            "total_skips": d["skips"],
            "sessions": d["sessions"],
            "avg_time_per_q_sec": avg_t,
            "band": score_band(avg)
        }

    # Subject summary
    subject_summary = {}
    for subj, d in subject_data.items():
        avg = round(sum(d["scores"]) / len(d["scores"]), 2) if d["scores"] else 0
        subject_summary[subj] = {"avg_score_pct": avg, "sessions": d["sessions"],
                                  "band": score_band(avg)}

    # Trends
    scores_over_time = []
    for sess in sorted(sessions, key=lambda s: s.get("date", "")):
        scores_over_time.append({
            "date": sess.get("date"),
            "subject": sess.get("subject"),
            "pct": parse_marks(sess.get("marks", "0"))["pct"],
            "chapters": sess.get("chapters", [])
        })

    overall_avg = round(sum(e["pct"] for e in scores_over_time) / len(scores_over_time), 2) \
        if scores_over_time else 0
    completion_rate = round(sum(completion_list) / len(completion_list) * 100, 1) \
        if completion_list else 0
    avg_speed = round(sum(speed_data) / len(speed_data), 1) if speed_data else 0

    # Trend direction (last 3 vs first 3 sessions)
    if len(scores_over_time) >= 4:
        first_avg = sum(e["pct"] for e in scores_over_time[:2]) / 2
        last_avg = sum(e["pct"] for e in scores_over_time[-2:]) / 2
        trend = "improving" if last_avg > first_avg + 3 else \
                "declining" if last_avg < first_avg - 3 else "stable"
    else:
        trend = "insufficient_data"

    strengths = sorted([ch for ch, d in chapter_summary.items() if d["avg_score_pct"] >= 70],
                       key=lambda c: -chapter_summary[c]["avg_score_pct"])
    weaknesses = sorted([ch for ch, d in chapter_summary.items() if d["avg_score_pct"] < 60],
                        key=lambda c: chapter_summary[c]["avg_score_pct"])

    return {
        "overall_avg_pct": overall_avg,
        "completion_rate_pct": completion_rate,
        "avg_time_per_q_sec": avg_speed,
        "trend": trend,
        "total_sessions": len(sessions),
        "chapters": chapter_summary,
        "subjects": subject_summary,
        "strengths": strengths[:5],
        "weaknesses": weaknesses[:5],
        "scores_over_time": scores_over_time
    }


def score_band(pct: float) -> str:
    if pct < 35:   return "critical"
    if pct < 55:   return "developing"
    if pct < 70:   return "average"
    if pct < 85:   return "good"
    return "excellent"


# ─── Utility: Build Recommendations ───────────────────────────────────────────
def build_recommendations(student: dict, profile: dict, dost_config: dict,
                           question_bank: list) -> list:
    """
    Generate step-by-step DOST recommendations based on student profile.
    Each step: dost_type, target_chapter, parameters, question_ids, reasoning, message.
    """
    weaknesses = profile["weaknesses"]
    avg_time = profile["avg_time_per_q_sec"]
    completion_rate = profile["completion_rate_pct"]
    chapter_data = profile["chapters"]
    dost_types = dost_config["dost_types"]
    steps = []
    step_num = 1

    # Index question bank by topic and subject
    qs_by_topic = defaultdict(list)
    qs_by_subject = defaultdict(list)
    for q in question_bank:
        q_id = normalize_id(q.get("_id", ""))
        # prefer explicit question_id field
        q_id_display = q.get("question_id", q_id)
        topic = q.get("topic", "").lower().replace(" ", "_")
        subj = q.get("subject", "")
        difficulty = q.get("difficulty")
        if difficulty is not None and q.get(q.get("questionType", ""), {}) and \
           q.get(q.get("questionType", ""), {}).get("answer") is not None:
            qs_by_topic[topic].append({"id": q_id_display, "difficulty": difficulty,
                                        "type": q.get("questionType"), "subject": subj})
            qs_by_subject[subj].append({"id": q_id_display, "difficulty": difficulty,
                                         "topic": topic, "type": q.get("questionType")})

    def get_questions_for_chapter(chapter_name: str, subject: str, count: int = 5,
                                   difficulty_range: tuple = (1, 5)) -> list:
        """Fetch relevant question IDs for a chapter."""
        topic_key = chapter_name.lower().replace(" ", "_").replace("-", "_")
        # Try exact topic match first
        qs = [q["id"] for q in qs_by_topic.get(topic_key, [])
              if difficulty_range[0] <= (q.get("difficulty") or 3) <= difficulty_range[1]]
        if not qs:
            # Fall back to subject-level
            qs = [q["id"] for q in qs_by_subject.get(subject, [])
                  if difficulty_range[0] <= (q.get("difficulty") or 3) <= difficulty_range[1]]
        return qs[:count]

    # ── Step logic ─────────────────────────────────────────────────────────────
    # For each weak chapter: concept → formula → assignment → test progression
    for chapter in weaknesses[:3]:
        band = chapter_data.get(chapter, {}).get("band", "developing")
        score = chapter_data.get(chapter, {}).get("avg_score_pct", 0)
        avg_ch_time = chapter_data.get(chapter, {}).get("avg_time_per_q_sec", avg_time)
        subject_guess = _guess_subject(chapter)

        if band == "critical":
            # Step A: Concept first
            steps.append({
                "step": step_num,
                "dost_type": "concept",
                "target_chapter": chapter,
                "subject": subject_guess,
                "parameters": dost_types["concept"]["parameters"],
                "question_ids": [],
                "reasoning": (f"{chapter} is in the CRITICAL band (score: {score:.1f}%). "
                               "The student needs foundational concept clarity before attempting questions."),
                "message": (f"Hey! Your score in {chapter} is {score:.1f}% — let's fix that. "
                             "Start by reading the concept explanation carefully. "
                             "Understanding the 'why' will make everything else easier! 💡")
            })
            step_num += 1

            # Step B: Formula sheet
            steps.append({
                "step": step_num,
                "dost_type": "formula",
                "target_chapter": chapter,
                "subject": subject_guess,
                "parameters": dost_types["formula"]["parameters"],
                "question_ids": [],
                "reasoning": f"After concepts, formula revision for {chapter} will anchor key equations.",
                "message": (f"Now go through the formula sheet for {chapter}. "
                             "Keep it open while you practice — you'll memorize faster by using them! 📋")
            })
            step_num += 1

            # Step C: Practice assignment (easy questions)
            q_ids = get_questions_for_chapter(chapter, subject_guess, 10, (1, 2))
            steps.append({
                "step": step_num,
                "dost_type": "practiceAssignment",
                "target_chapter": chapter,
                "subject": subject_guess,
                "parameters": {**dost_types["practiceAssignment"]["parameters"],
                                "total_questions": {"default": 10},
                                "difficulty_mix": {"easy": 0.7, "medium": 0.3, "hard": 0.0}},
                "question_ids": q_ids,
                "reasoning": (f"Easy-first practice assignment for {chapter} to build confidence "
                               "before tackling harder questions."),
                "message": (f"Time to apply what you learned! Here are 10 practice questions on {chapter}. "
                             "No timer — take your time. Questions are easy to medium. You got this! 💪")
            })
            step_num += 1

        elif band == "developing":
            # Step A: Targeted assignment
            q_ids = get_questions_for_chapter(chapter, subject_guess, 12, (2, 4))
            steps.append({
                "step": step_num,
                "dost_type": "practiceAssignment",
                "target_chapter": chapter,
                "subject": subject_guess,
                "parameters": {**dost_types["practiceAssignment"]["parameters"],
                                "total_questions": {"default": 12}},
                "question_ids": q_ids,
                "reasoning": (f"{chapter} is in DEVELOPING band (score: {score:.1f}%). "
                               "Targeted practice will help identify specific gaps."),
                "message": (f"Your {chapter} score is {score:.1f}% — room for improvement! "
                             "Work through this assignment at your own pace. "
                             "Pay attention to the solutions for questions you get wrong. 🎯")
            })
            step_num += 1

            # Step B: Picking power if negative marks are high
            neg_marks_sessions = [parse_marks(s["marks"])["negative"]
                                   for s in student["sessions"]
                                   if chapter in s.get("chapters", [])]
            avg_neg = sum(neg_marks_sessions) / len(neg_marks_sessions) if neg_marks_sessions else 0
            if avg_neg > 8:
                steps.append({
                    "step": step_num,
                    "dost_type": "pickingPower",
                    "target_chapter": chapter,
                    "subject": subject_guess,
                    "parameters": dost_types["pickingPower"]["parameters"],
                    "question_ids": get_questions_for_chapter(chapter, subject_guess, 8, (2, 3)),
                    "reasoning": (f"High negative marks detected (~{avg_neg:.0f} per session). "
                                   "Option elimination practice will reduce guessing."),
                    "message": (f"You're losing marks to wrong answers in {chapter}. "
                                 "Practice MCQ option elimination — it's a game-changer for accuracy! 🎲")
                })
                step_num += 1

        else:  # average (55–70)
            # Focused test
            q_ids = get_questions_for_chapter(chapter, subject_guess, 15, (2, 4))
            steps.append({
                "step": step_num,
                "dost_type": "practiceTest",
                "target_chapter": chapter,
                "subject": subject_guess,
                "parameters": {**dost_types["practiceTest"]["parameters"],
                                "total_questions": {"default": 15},
                                "duration_minutes": {"default": 20}},
                "question_ids": q_ids,
                "reasoning": (f"{chapter} is in AVERAGE band (score: {score:.1f}%). "
                               "A focused timed test will push it to 'good'."),
                "message": (f"{chapter} is close! Take this 20-minute focused test to push past 70%. "
                             "Treat it like the real exam. 🏆")
            })
            step_num += 1

    # ── Speed improvement ───────────────────────────────────────────────────────
    if avg_time > 160:
        steps.append({
            "step": step_num,
            "dost_type": "clickingPower",
            "target_chapter": "Speed Improvement",
            "subject": "General",
            "parameters": dost_types["clickingPower"]["parameters"],
            "question_ids": [q["id"] for q in list(qs_by_subject.values())[0][:10]
                              if q.get("difficulty", 3) <= 2] if qs_by_subject else [],
            "reasoning": (f"Average time per question is {avg_time:.0f}s — above the 160s threshold. "
                           "Speed drills on easy questions build automaticity."),
            "message": (f"You're spending {avg_time:.0f}s per question on average — that's too slow for JEE/NEET! "
                         "Do this 10-question speed drill daily. Aim for under 90s per question. ⚡")
        })
        step_num += 1

    # ── Completion / consistency ────────────────────────────────────────────────
    if completion_rate < 70:
        steps.append({
            "step": step_num,
            "dost_type": "revision",
            "target_chapter": "Multiple Weak Chapters",
            "subject": "All",
            "parameters": dost_types["revision"]["parameters"],
            "question_ids": [],
            "reasoning": (f"Completion rate is {completion_rate:.0f}% — student is abandoning sessions. "
                           "A structured revision plan builds discipline and reduces overwhelm."),
            "message": (f"You're finishing only {completion_rate:.0f}% of your sessions — "
                         "incomplete attempts hurt your preparation more than wrong answers. "
                         "This revision plan will give you clear daily targets. Let's be consistent! 📅")
        })
        step_num += 1

    # ── Speed race for motivation (if doing reasonably well) ───────────────────
    overall = profile["overall_avg_pct"]
    if overall >= 55 and completion_rate >= 70:
        steps.append({
            "step": step_num,
            "dost_type": "speedRace",
            "target_chapter": "Strengths Reinforcement",
            "subject": "General",
            "parameters": dost_types["speedRace"]["parameters"],
            "question_ids": [],
            "reasoning": ("Student is performing reasonably well. A speed race on strong topics "
                           "boosts confidence and exam speed simultaneously."),
            "message": ("You're doing well! Challenge yourself in a Speed Race on your strong topics. "
                         "Beat the bot and build that exam-day confidence! 🚀")
        })

    if not steps:
        steps.append({
            "step": 1,
            "dost_type": "practiceTest",
            "target_chapter": "All Subjects",
            "subject": "All",
            "parameters": dost_types["practiceTest"]["parameters"],
            "question_ids": [],
            "reasoning": "No critical weaknesses detected. Full mock test to benchmark performance.",
            "message": "Great performance across chapters! Take a full mock test to benchmark yourself. 🌟"
        })

    return steps


def _guess_subject(chapter_name: str) -> str:
    """Heuristic subject guess from chapter name."""
    physics_kw = ["kinematics", "mechanics", "thermodynamics", "electrostatics", "optics",
                   "waves", "magnetism", "fluid", "gravitation", "rotational", "current",
                   "electromagnetic", "modern physics", "shm", "motion", "laws of motion",
                   "work energy", "heat", "nuclear", "semiconductor", "ac circuits",
                   "bernoulli", "kinetic theory", "projectile", "circular"]
    chem_kw = ["chemistry", "organic", "inorganic", "stoichiometry", "mole", "atomic",
                "bonding", "equilibrium", "electrochemistry", "kinetics", "polymers",
                "biomolecules", "periodic", "redox", "solid state", "solutions", "colloids",
                "gaseous", "surface", "coordination", "amines", "diazonium", "p-block",
                "d-block", "s-block", "hydrocarbons", "isomerism"]
    bio_kw = ["cell", "genetics", "ecology", "heredity", "evolution", "biology",
               "plant", "animal", "physiology", "biotechnology", "diversity"]

    ch_lower = chapter_name.lower()
    for kw in physics_kw:
        if kw in ch_lower: return "Physics"
    for kw in chem_kw:
        if kw in ch_lower: return "Chemistry"
    for kw in bio_kw:
        if kw in ch_lower: return "Biology"
    return "Mathematics"


# ─── Leaderboard Scoring ───────────────────────────────────────────────────────
def compute_leaderboard_score(profile: dict, sessions: list) -> dict:
    """
    Scoring formula:
        base_score        = overall_avg_pct           (0–100)     weight 40%
        consistency_bonus = completion_rate_pct / 100 * 20        weight 20%
        speed_bonus       = max(0, (180 - avg_time) / 180) * 15   weight 15%
        coverage_bonus    = (subjects covered / 4) * 15           weight 15%
        trend_bonus       = +5 if improving, 0 stable, -5 declining weight 10%
    Total out of 100.
    """
    base = profile["overall_avg_pct"] * 0.40
    consistency = (profile["completion_rate_pct"] / 100) * 20
    speed_factor = max(0, (180 - profile["avg_time_per_q_sec"]) / 180)
    speed = speed_factor * 15
    subjects_covered = len(profile["subjects"])
    coverage = min(subjects_covered / 4, 1.0) * 15
    trend_map = {"improving": 10, "stable": 5, "declining": 0, "insufficient_data": 5}
    trend = trend_map.get(profile["trend"], 5)
    total = round(base + consistency + speed + coverage + trend, 2)
    return {
        "total_score": total,
        "breakdown": {
            "performance_40pct": round(base, 2),
            "consistency_20pct": round(consistency, 2),
            "speed_15pct": round(speed, 2),
            "coverage_15pct": round(coverage, 2),
            "trend_10pct": trend
        }
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "Acadza Recommender API is running. Visit /docs for Swagger UI."}


@app.post("/analyze/{student_id}")
def analyze_student(student_id: str):
    """
    Analyze a student's performance across all sessions.
    Returns patterns, trends, chapter-wise breakdown, strengths, weaknesses.
    """
    students = load_students()
    student = next((s for s in students if s["student_id"] == student_id), None)
    if not student:
        raise HTTPException(status_code=404, detail=f"Student '{student_id}' not found.")

    profile = build_profile(student)

    return {
        "student_id": student_id,
        "name": student.get("name", ""),
        "analysis": {
            "overall_avg_pct": profile["overall_avg_pct"],
            "completion_rate_pct": profile["completion_rate_pct"],
            "avg_time_per_q_sec": profile["avg_time_per_q_sec"],
            "performance_trend": profile["trend"],
            "total_sessions": profile["total_sessions"],
            "subjects": profile["subjects"],
            "chapters": profile["chapters"],
            "strengths": profile["strengths"],
            "weaknesses": profile["weaknesses"],
            "scores_over_time": profile["scores_over_time"],
            "summary": _generate_summary(student.get("name",""), profile)
        }
    }


def _generate_summary(name: str, profile: dict) -> str:
    avg = profile["overall_avg_pct"]
    trend = profile["trend"]
    comp = profile["completion_rate_pct"]
    weaknesses = profile["weaknesses"]
    strengths = profile["strengths"]

    summary = f"{name} has an overall average of {avg:.1f}% across {profile['total_sessions']} sessions. "
    summary += f"Performance trend is {trend}. Completion rate: {comp:.0f}%. "
    if strengths:
        summary += f"Strong in: {', '.join(strengths[:2])}. "
    if weaknesses:
        summary += f"Needs attention: {', '.join(weaknesses[:2])}."
    return summary


@app.post("/recommend/{student_id}")
def recommend_for_student(student_id: str):
    """
    Return a step-by-step personalized study plan for a student.
    Each step includes: DOST type, target chapter, parameters, specific question IDs,
    reasoning, and a motivational message to the student.
    """
    students = load_students()
    student = next((s for s in students if s["student_id"] == student_id), None)
    if not student:
        raise HTTPException(status_code=404, detail=f"Student '{student_id}' not found.")

    question_bank = load_question_bank()
    dost_config = load_dost_config()
    profile = build_profile(student)
    steps = build_recommendations(student, profile, dost_config, question_bank)

    return {
        "student_id": student_id,
        "name": student.get("name", ""),
        "overall_avg_pct": profile["overall_avg_pct"],
        "completion_rate_pct": profile["completion_rate_pct"],
        "top_weaknesses": profile["weaknesses"][:3],
        "top_strengths": profile["strengths"][:3],
        "recommendation_plan": steps,
        "total_steps": len(steps)
    }


@app.get("/question/{question_id}")
def get_question(question_id: str):
    """
    Look up a question by ID. Normalizes _id field.
    Returns clean data with plaintext preview (strips HTML tags).
    """
    question_bank = load_question_bank()

    def strip_html(text: str) -> str:
        return re.sub(r'<[^>]+>', ' ', text or '').strip()

    for q in question_bank:
        raw_id = q.get("_id", "")
        norm_id = normalize_id(raw_id)
        explicit_id = q.get("question_id", "")
        if norm_id == question_id or explicit_id == question_id:
            qtype = q.get("questionType", "scq")
            content = q.get(qtype, {}) or {}
            question_text = strip_html(content.get("question", ""))
            solution_text = strip_html(content.get("solution", ""))
            answer = content.get("answer")
            return {
                "question_id": explicit_id or norm_id,
                "normalized_id": norm_id,
                "subject": q.get("subject"),
                "topic": q.get("topic"),
                "subtopic": q.get("subtopic"),
                "difficulty": q.get("difficulty"),
                "question_type": qtype,
                "question_preview": question_text[:300] + ("..." if len(question_text) > 300 else ""),
                "solution_preview": solution_text[:300] + ("..." if len(solution_text) > 300 else ""),
                "answer": answer,
                "has_answer": answer is not None,
                "data_quality": "clean" if (answer is not None and q.get("difficulty") is not None) else "has_issues"
            }

    raise HTTPException(status_code=404, detail=f"Question '{question_id}' not found.")


@app.get("/leaderboard")
def get_leaderboard():
    """
    Rank all 10 students using a composite scoring formula.
    Formula: 40% avg_score + 20% completion + 15% speed + 15% subject_coverage + 10% trend
    """
    students = load_students()
    results = []
    for student in students:
        profile = build_profile(student)
        score_info = compute_leaderboard_score(profile, student["sessions"])
        results.append({
            "student_id": student["student_id"],
            "name": student.get("name", ""),
            "rank": 0,
            "composite_score": score_info["total_score"],
            "score_breakdown": score_info["breakdown"],
            "overall_avg_pct": profile["overall_avg_pct"],
            "completion_rate_pct": profile["completion_rate_pct"],
            "avg_time_per_q_sec": profile["avg_time_per_q_sec"],
            "trend": profile["trend"],
            "top_strength": profile["strengths"][0] if profile["strengths"] else "N/A",
            "top_weakness": profile["weaknesses"][0] if profile["weaknesses"] else "N/A",
            "focus_area": profile["weaknesses"][0] if profile["weaknesses"] else
                          profile["strengths"][0] if profile["strengths"] else "Balanced"
        })

    # Sort by composite score descending
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    return {
        "leaderboard": results,
        "scoring_formula": {
            "description": "Composite score out of 100",
            "components": {
                "performance": "40% — overall average score across all sessions",
                "consistency": "20% — session completion rate",
                "speed": "15% — inverse of avg time per question (faster = higher)",
                "coverage": "15% — number of distinct subjects covered (max 4)",
                "trend": "10% — improving=10, stable=5, declining=0"
            }
        }
    }
