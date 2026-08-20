"""Generate and persist the prediction for the latest completed market session."""

from app import get_prediction
from database import record_prediction


def main() -> None:
    prediction, _, _ = get_prediction(force=True)
    if not prediction.market_session_complete:
        print(
            f"Skipped {prediction.market_session_date}: the market session is not complete."
        )
        return

    saved = record_prediction(prediction)
    outcome = "saved" if saved else "already recorded"
    print(
        f"Prediction {outcome}: model={prediction.model_version} "
        f"observed={prediction.market_session_date} forecast_for={prediction.forecast_for}"
    )


if __name__ == "__main__":
    main()
