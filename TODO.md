# XSP Predictor Evaluation Plan

We should freeze the experiment before accumulating more history.

## Game plan

- [ ] Add a `model_version` to every prediction. Otherwise, future improvements get mixed together and the accuracy becomes misleading.
- [ ] Generate exactly one prediction per completed market session, ideally after 4:15 PM New York time.
- [ ] Settle each prediction after the following market session closes.
- [ ] Compare the model against these baselines:
  - Always predict `UP`
  - Previous-day momentum
  - A 50/50 probability forecast
  - The model's walk-forward backtest
- [ ] Avoid changing the model based on a handful of misses. That would overfit the live sample.
- [ ] Review results only at these predefined checkpoints:
  - 20 predictions: pipeline sanity check only
  - 60 predictions: preliminary evidence
  - 125 predictions: useful midyear assessment
  - 250 predictions: approximately one trading year and a more credible evaluation
  - 500+ predictions: better coverage of different market conditions

## Metrics to track

- [ ] Directional accuracy
- [ ] Balanced accuracy
- [ ] Brier score for probability quality
- [ ] Accuracy when confidence exceeds 55%, 60%, and other predefined thresholds
- [ ] Performance in high- and low-volatility conditions
- [ ] Performance relative to the always-up baseline

## Experimental expectations

The current historical walk-forward model underperformed the naïve baselines, so our prior expectation should be that it has no edge. The forward ledger will tell us whether the complete live process—including current news—performs differently.

Before additional observations accumulate, add model versioning and preserve the exact component scores used for every call. After that, freeze the model and wait without repeatedly tuning it.
