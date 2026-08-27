# Procedural M1/M8 v4 development A/B

Acceptance passed: **True**

| Arm | Successes | Tasks | Rate | Wilson 95% |
|---|---:|---:|---:|---|
| M1 | 0 | 5 | 0.0000 | [0.0, 0.434482] |
| M8 | 5 | 5 | 1.0000 | [0.565518, 1.0] |

```json
{
  "acceptance_checks": {
    "cluster_intervals": true,
    "complete_obligation_coverage": true,
    "m1_m8_success_delta_positive": true,
    "m8_harmful_rate": true,
    "m8_successes": true,
    "min_tasks": true
  },
  "cluster_summary": {
    "M1": {
      "clusters": 5,
      "rate": 0.0,
      "successes": 0,
      "wilson_95": [
        0.0,
        0.434482
      ]
    },
    "M8": {
      "clusters": 5,
      "rate": 1.0,
      "successes": 5,
      "wilson_95": [
        0.565518,
        1.0
      ]
    }
  }
}
```
