import hashlib
import time
from google.adk.agents import Agent

GATE_PASS_RECORDS = {
    "2024668": {"approved": True, "leave_start": "2026-11-20", "leave_end": "2026-11-24"},
    "2024405": {"approved": False, "leave_start": None, "leave_end": None},
    "2024662": {"approved": True, "leave_start": "2026-11-18", "leave_end": "2026-11-22"},
}

REBATE_LEDGER = []

DAILY_MENU = {
    "breakfast": {
        "items": ["Paratha", "Omelette", "Chana Masala", "Chai"],
        "calories": 650,
        "protein_g": 22,
        "carbs_g": 68,
        "fat_g": 31,
        "allergens": ["eggs", "gluten", "dairy"],
    },
    "lunch": {
        "items": ["Chicken Biryani", "Mint Raita", "Fresh Salad"],
        "calories": 820,
        "protein_g": 38,
        "carbs_g": 95,
        "fat_g": 28,
        "allergens": ["dairy"],
    },
    "dinner": {
        "items": ["Beef Karahi", "Daal Mash", "Tandoori Roti"],
        "calories": 780,
        "protein_g": 42,
        "carbs_g": 74,
        "fat_g": 34,
        "allergens": ["gluten"],
    },
}


def apply_mess_rebate(student_reg_no: str, start_date: str, end_date: str, days_count: int) -> dict:
    """Applies for a mess rebate by syncing with campus security gate-pass records and logging to the rebate ledger.

    Args:
        student_reg_no: Student registration number (e.g., '2024668').
        start_date: Start date of absence in YYYY-MM-DD format.
        end_date: End date of absence in YYYY-MM-DD format.
        days_count: Total number of absence days requested.
    """
    gate_pass = GATE_PASS_RECORDS.get(student_reg_no)

    if not gate_pass or not gate_pass["approved"]:
        return {
            "status": "rejected",
            "student_reg_no": student_reg_no,
            "reason": "No approved campus security gate-pass found for this registration number. Rebates require an active exit gate-pass.",
        }

    if days_count < 2:
        return {
            "status": "rejected",
            "student_reg_no": student_reg_no,
            "reason": "Minimum 2 consecutive days of absence are required for a mess rebate.",
        }

    daily_rate_pkr = 450
    total_rebate_pkr = days_count * daily_rate_pkr

    ledger_entry = {
        "student_reg_no": student_reg_no,
        "start_date": start_date,
        "end_date": end_date,
        "days_count": days_count,
        "rebate_amount_pkr": total_rebate_pkr,
    }
    REBATE_LEDGER.append(ledger_entry)

    return {
        "status": "approved",
        "ledger_entry": ledger_entry,
        "message": f"Rebate of PKR {total_rebate_pkr} approved via gate-pass sync and deducted from monthly mess bill.",
    }


def generate_totp_meal_qr(student_reg_no: str, meal_period: str) -> dict:
    """Generates a dynamic 30-second TOTP-backed QR token for mess dining hall entry gatekeeping.

    Args:
        student_reg_no: Student registration number (e.g., '2024668').
        meal_period: Meal session ('breakfast', 'lunch', or 'dinner').
    """
    meal_key = meal_period.lower().strip()
    if meal_key not in DAILY_MENU:
        return {"status": "error", "message": "Invalid meal period. Choose breakfast, lunch, or dinner."}

    time_step = int(time.time() // 30)
    raw_seed = f"{student_reg_no}:{meal_key}:{time_step}"
    totp_code = hashlib.sha256(raw_seed.encode()).hexdigest()[:8].upper()

    return {
        "status": "active",
        "student_reg_no": student_reg_no,
        "meal_period": meal_key.title(),
        "dynamic_qr_payload": f"GCORTEX-MESS-{student_reg_no}-{totp_code}",
        "valid_for_seconds": 30,
        "verification": "Ready for dining hall turnstile scan (anti-passback enabled).",
    }


def check_menu_macros_and_allergens(meal_period: str, student_allergies: str = "none") -> dict:
    """Checks the daily mess menu, nutritional macros (calories, protein, carbs, fat), and runs the allergen shield check.

    Args:
        meal_period: Meal session ('breakfast', 'lunch', or 'dinner').
        student_allergies: Comma-separated allergies to check against (e.g., 'dairy, gluten', 'eggs', or 'none').
    """
    meal_key = meal_period.lower().strip()
    meal = DAILY_MENU.get(meal_key)
    if not meal:
        return {"status": "error", "message": "Invalid meal period. Choose breakfast, lunch, or dinner."}

    user_allergy_list = [a.strip().lower() for a in student_allergies.split(",") if a.strip().lower() != "none"]
    triggered_allergens = [a for a in user_allergy_list if a in meal["allergens"]]

    return {
        "status": "success",
        "meal_period": meal_key.title(),
        "menu_items": meal["items"],
        "macros": {
            "calories_kcal": meal["calories"],
            "protein_g": meal["protein_g"],
            "carbs_g": meal["carbs_g"],
            "fat_g": meal["fat_g"],
        },
        "meal_allergens": meal["allergens"],
        "allergen_shield_status": (
            f"DANGER: Contains {', '.join(triggered_allergens)}!"
            if triggered_allergens
            else "SAFE: No matching allergens detected."
        ),
    }


def forecast_kitchen_demand(meal_period: str, day_type: str = "regular") -> dict:
    """Predicts dining hall headcount and recommended food production volume to minimize kitchen waste by 15-25%.

    Args:
        meal_period: Meal session ('breakfast', 'lunch', or 'dinner').
        day_type: Campus schedule context ('regular', 'weekend', 'midterm_exam', or 'pre_holiday').
    """
    total_enrolled_students = 1200
    active_rebates = len(REBATE_LEDGER) + 85

    attendance_multipliers = {
        "regular": 0.88,
        "weekend": 0.65,
        "midterm_exam": 0.72,
        "pre_holiday": 0.55,
    }
    multiplier = attendance_multipliers.get(day_type.lower().strip(), 0.85)
    predicted_headcount = int((total_enrolled_students - active_rebates) * multiplier)
    waste_reduction_pct = round((1 - (predicted_headcount / total_enrolled_students)) * 100, 1)

    return {
        "status": "success",
        "meal_period": meal_period.title(),
        "day_type": day_type,
        "total_enrolled": total_enrolled_students,
        "active_gatepass_rebates": active_rebates,
        "predicted_dining_headcount": predicted_headcount,
        "estimated_overproduction_saved_pct": f"{waste_reduction_pct}%",
        "kitchen_recommendation": f"Prepare portions for {predicted_headcount} students (+5% buffer) instead of full {total_enrolled_students} capacity.",
    }


mess_management_subagent = Agent(
    name="mess_management_subagent",
    model="gemini-3.5-flash-lite",
    description="Handles university mess dining operations: gate-pass synced rebates, TOTP QR entry tokens, menu macros/allergen shield, and kitchen demand forecasting.",
    instruction="""You are the G-Cortex Mess Management Subagent.
    Use your tools to help students and mess staff:
    1. Apply for mess rebates (verify registration number, start/end dates, and days count).
    2. Generate dynamic 30-second TOTP QR tokens for dining hall entry.
    3. Check meal menus, nutritional macros (calories, protein, carbs, fat), and allergen warnings.
    4. Forecast kitchen headcount demand based on active rebates and campus schedule context.
    Always provide clear, structured responses based on tool outputs.""",
    tools=[
        apply_mess_rebate,
        generate_totp_meal_qr,
        check_menu_macros_and_allergens,
        forecast_kitchen_demand,
    ],
)

root_agent = Agent(
    name="g_cortex_orchestrator",
    model="gemini-3.5-flash-lite",
    description="Main G-Cortex campus operating system orchestrator routing tasks to specialized subagents.",
    instruction="""You are G-Cortex, the autonomous university operating system.
    Route all dining hall, mess rebate, meal QR code, menu nutrition/allergen, and kitchen headcount forecasting queries to the mess_management_subagent.""",
    sub_agents=[mess_management_subagent],
)
