# Acadza Student Recommender System

**Author:** Chandan Shetty  
**Assignment:** Acadza AI Intern — April 2026  
**Stack:** Python · FastAPI · Uvicorn

---

## Setup & Running
```bash
# 1. Clone / unzip the project
cd acadza-recommender

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Open Swagger UI
http://localhost:8000/docs
```

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/analyze/{student_id}` | Full performance analysis |
| POST | `/recommend/{student_id}` | Step-by-step DOST study plan |
| GET | `/question/{question_id}` | Look up a question |
| GET | `/leaderboard` | Rank all students |

Student IDs: `STU_001` through `STU_010`

---

## My Approach — How I Built This

### Understanding the Problem First

When I read the problem statement, I recognized that this isn't just a CRUD API — it's a system that needs to make *pedagogically sound* decisions about what a student should do next. That framing shaped everything else.

The first thing I did was map out the full data flow: raw session records → normalized performance profile → weakness detection → DOST selection → question assignment. Each step needed to be solid before the next one could work.

### Handling Student Performance Data

Each student has 5–8 sessions, and each session has a mess of fields. The most important one is `marks` — and it's intentionally inconsistent across sessions (`"+52 -8"`, `"68/100"`, `"34/75 (45.3%)"`, `72`, `"28"`).

My `parse_marks()` function handles all five formats and always returns a **percentage (0–100)**. Here's the logic I followed:

- `"+52 -8"` → positive minus negative, divided by the sum, times 100. This gives the *net score as a fraction of total marks attempted*.
- `"68/100"` and `"34/75 (45.3%)"` → straightforward numerator/denominator extraction, with the parenthetical percentage ignored (it's redundant).
- Bare numbers like `"72"` or the integer `28` → I treat these as marks out of 100. **This is the documented assumption**: bare numbers without a denominator are assumed to be out of 100. If they were out of a different total, the data should have been recorded as a fraction — that's a data-quality issue upstream.

The critical insight was that every `parse_marks()` call must return the same *unit* — percentages — so that chapter averages across sessions with different formats are comparable.

### Building the Student Profile

For each student, I compute:

- **Chapter-level stats**: average score percentage, total questions attempted, skip count, sessions count, average time per question
- **Subject-level stats**: same, aggregated by subject
- **Trend**: I compare the average of the first two sessions vs. the last two sessions. If the gap is more than 3 percentage points up, the student is "improving"; more than 3 points down, "declining"; otherwise "stable"
- **Speed**: average time per question across recent sessions
- **Completion rate**: fraction of sessions the student actually finished vs. abandoned

The profile is the foundation for everything else — analysis, recommendations, and leaderboard scoring.

### Recommending DOSTs

My recommendation logic maps each weak chapter to a sequence of DOSTs based on severity:

**Critical band (< 35%):** The student clearly doesn't understand the concept. I prescribe concept → formula sheet → easy practice assignment, in that order. No point throwing hard questions at someone who hasn't built the mental model yet.

**Developing band (35–55%):** The student has some grasp but is losing marks. I assign a targeted practice assignment (medium difficulty). If their negative marks are high, I additionally add a `pickingPower` DOST to train option elimination and reduce careless negative marking.

**Average band (55–70%):** The student is close to "good". A focused timed test — same conditions as the real exam — is enough to close the gap.

Beyond chapter-specific DOSTs, I add:

- **`clickingPower`** if average time per question > 160 seconds. In JEE Mains, students have roughly 120 seconds per question. If they're regularly at 160–200 seconds, they'll run out of time under exam conditions.
- **`revision`** if completion rate < 70%. A student who abandons sessions regularly needs structured daily targets more than more practice questions.
- **`speedRace`** for students who are already doing well (overall > 55%, completion > 70%) — a confidence and engagement booster.

Each recommendation step includes the DOST type, the target chapter, the config parameters (pulled from `dost_config.json`), specific question IDs from the bank (filtered by topic, subject, and difficulty range), reasoning for the system, and a direct message to the student.

### Leaderboard Scoring Formula

I designed a 5-component composite score out of 100:

| Component | Weight | Rationale |
|-----------|--------|-----------|
| Average score | 40% | Core academic performance |
| Completion rate | 20% | Consistency and discipline matter |
| Speed | 15% | Faster solvers perform better under exam time pressure |
| Subject coverage | 15% | Breadth of preparation |
| Trend | 10% | Reward improvement; penalize regression |

### Handling Data Quality Issues

The question bank has ~10% problematic entries — null difficulty, missing answers, duplicate `_id`s. My `get_question` endpoint normalizes both `{"$oid": "..."}` and flat string `_id` formats. When fetching questions for recommendations, I filter out those with `null` difficulty or missing answers so students only get served clean questions.

---

## Debug Task — The Bug in `recommender_buggy.py`

**The bug is in `parse_marks()`, in the bare-number branch.**

The original code:
```python
try:
    val = float(marks_str)
    return val   # ← BUG
except ValueError:
    return 0.0
```

It returns the raw number directly. For `"+52 -8"` the function correctly returns `73.33%`. For `"68/100"` it correctly returns `68.0%`. But for a bare `"72"`, it returns `72.0` — *which looks like a percentage but is actually the raw mark value*. The function's contract is "return a percentage", but the bare-number branch breaks that contract silently.

**Why this is hard to spot:** The output looks completely reasonable. `72` feels like it could be 72%. The function doesn't crash. The recommendations it produces aren't obviously absurd. You'd only catch it by carefully cross-checking a student's raw session data against the profile scores it generates.

**How I found it:** I ran the buggy recommender on a student whose raw sessions I'd read manually and compared the chapter scores. A student with a session marked `"44"` (bare number, out of ~60 marks) was getting `avg_score_pct: 44.0`, but 44/60 = 73.3% — so they were being classified as "developing" when they were actually "good". Traced that back to `parse_marks("44") → 44.0`.

**The fix:** One line change — `return round(min(val, 100.0), 2)` instead of `return val`. Bare numbers are now treated as marks out of 100, consistent with how `"44/100"` would be handled.

---

## Assumptions Made

1. Bare-number marks are out of 100 unless expressed as a fraction.
2. `"+52 -8"` format: the denominator is `52 + 8 = 60` (total marks in play), not a fixed total.
3. Sessions with `completed: false` are included in the analysis but penalize the completion rate.
4. `_id` deduplication in the question bank: if two questions share the same `_id`, the first one is used.

---

## What I'd Improve Given More Time

1. **Real score normalization per exam pattern**: JEE Mains, JEE Advanced, and NEET have different marking schemes. Currently I normalize everything to a percentage. Ideally, the marks would be normalized using the exam pattern's marking scheme before computing percentages.

2. **Spaced repetition scheduling**: Instead of recommending chapters purely by score band, I'd incorporate how recently the student studied each chapter. A chapter studied two days ago needs less reinforcement than one from three weeks ago.

3. **Question difficulty calibration**: The `difficulty` field (1–5) in the question bank isn't verified. A proper recommender would use student performance *on specific questions* to calibrate actual difficulty via IRT (Item Response Theory) and serve questions matched to the student's ability level.

4. **Persistent state**: Currently the API reloads data on every request. In production, I'd cache the student profiles and invalidate on new sessions.

5. **Database backend**: Replace JSON files with a proper DB (PostgreSQL or MongoDB) so the system can scale beyond 10 students and support concurrent reads.

6. **Feedback loop**: After a student completes a recommended DOST, re-run the profiler and update the recommendation plan. Right now recommendations are one-shot.

---

## Project Structure
```
acadza-recommender/
├── app/
│   └── main.py              # FastAPI app — all 4 endpoints
├── data/
│   ├── student_performance.json
│   ├── question_bank.json
│   └── dost_config.json
├── debug/
│   ├── recommender_buggy.py  # Original with bug (preserved)
│   └── recommender_fixed.py  # Fixed version with explanation
├── sample_outputs/
│   ├── STU_001_analyze.json  # ... through STU_010
│   ├── STU_001_recommend.json
│   └── leaderboard.json
├── requirements.txt
└── README.md
```
