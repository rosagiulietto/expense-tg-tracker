"""
Monthly visual reports (Phase 4).

Generates a PNG bar chart of spending by category for a given local month.
matplotlib is imported lazily so the bot stays lightweight until a report is
actually requested.
"""

import io
from calendar import month_name

import database as db


def build_monthly_report(
    user_id: int, year: int, month: int, db_path: str = db.DB_NAME
) -> io.BytesIO:
    """Return a PNG image (BytesIO) with the category breakdown for the month."""
    total, breakdown = db.monthly_summary(user_id, year, month, db_path)
    title = f"{month_name[month]} {year}"

    # Matplotlib must use a non-interactive backend on a headless server.
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)

    if breakdown:
        # Largest category at the top.
        rows = list(reversed(breakdown))
        categories = [row["category"] for row in rows]
        amounts = [row["total"] for row in rows]

        bars = ax.barh(categories, amounts, color="#4C72B0", height=0.6)
        ax.bar_label(bars, fmt="%.2f", padding=3, fontsize=9)
        ax.set_xlim(0, max(amounts) * 1.18)
        ax.set_xlabel("Amount")
        ax.grid(axis="x", linestyle="--", alpha=0.4)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_title(
            f"Spending by category - {title}\nTotal: {total:.2f}", fontsize=13
        )
    else:
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            f"No expenses recorded\n{title}",
            ha="center",
            va="center",
            fontsize=14,
            color="#555555",
        )

    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer
