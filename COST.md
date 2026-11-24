# Cost, ROI and KPIs

What the CI Triage Agent costs to run, what it should save, and how success is measured. This file is the single
source for these numbers: any other document that quotes them must match it.

Labels: **[evidence]** measured or read from a record, source named. **[testimony]** stated by the team or author,
not measured. **[inference]** calculated from labelled numbers or published prices. **[no data]** not known yet;
nothing is estimated in its place.

## 1. Running cost

| Item | Figure | Label |
|---|---|---|
| Model | Claude Sonnet 5.5 (`claude-sonnet-5-5`), medium effort; `TRIAGE_MODEL` and `TRIAGE_EFFORT` change it | [evidence: `triage/llm.py`] |
| Price | $2 per million input tokens, $10 per million output tokens | [inference: not re-checked against Anthropic's price list] |
| Single-call diagnosis (`hybrid` mode) | About $0.017 (6,547 input and 354 output tokens) | [evidence: measured on the sandbox pipeline] |
| Failure decided by a rule | No model call, $0 | [evidence: `tests/test_diagnose.py`] |
| Model cost per week | At most about $0.25 (15 failures × $0.017, every one sent to the model) | [inference] |
| Investigation (`agent` mode: several calls plus tool results) | Higher than $0.017, capped at 6 tool turns (`TRIAGE_MAX_STEPS`). Exact figure: | [no data: not yet measured] |
| GitHub Actions minutes | About 17 s per triage run, 4 to 5 minutes a week; free on public repositories | [evidence: run 36491776923], [inference: weekly] |
| Slack, maintenance (rules, prompt, model upgrades) | | [no data] |

Each diagnosis record saves `usage` (`input_tokens`, `output_tokens`, `model_calls`) and `steps`, so the cost of
every diagnosis can be computed afterwards. The 12 of 12 planted failures and the 25-second time to Slack (median,
range 16 to 51 s) [evidence] come from planted failures repeated: they show the loop works, not accuracy on real
failures [no data].

**Anthropic spend, read from the Console on 2026-11-24:** $5.00 credit granted 2026-09-28; $4.02 left; about $0.98
spent in total [inference: 5.00 − 4.02]; $0.29 in the last 30 days, all on Claude Sonnet 5.5 [evidence]. Auto-reload
is off, so requests stop when the balance runs out [evidence].

## 2. AWS cloud cost

Region `us-east-1`. Everything is created by `terraform/` and removed by `terraform destroy`.

| Resource | Cost | Label |
|---|---|---|
| GitHub OIDC provider, two IAM roles, one managed policy | No charge; no IAM line in the account's Cost Explorer for the last 30 days | [evidence: Cost Explorer], [inference: IAM is not billed] |
| Sandbox S3 bucket | Empty so far (0 objects). All S3 in the whole account cost $0.00000007 in the last 30 days, so this bucket cost at most that. At about 35 uploads a week of a tiny file: under one cent a month. | [evidence: `aws s3 ls`, Cost Explorer], [inference: S3 request price, not re-checked] |
| CloudTrail event history (the agent's `search_cloudtrail`) | No trail was created; the 90-day management-event history is included. No CloudTrail line in Cost Explorer. | [evidence: Cost Explorer], [inference: pricing] |
| Total attributable to this project, last 30 days | Under $0.01 | [inference] |
| The check itself | One Cost Explorer query is billed about $0.01 | [inference: AWS pricing] |
| Running the agent on AWS (for example a function) | Not built: the agent runs on GitHub Actions | [no data] |

Costs cannot be filtered by this project: Terraform tags every resource `Project=ci-triage-agent`, but the tag is not
activated for cost allocation [evidence: `aws ce list-cost-allocation-tags` returned none]. A budget alert named
"Minimum" exists on the account [evidence: AWS CLI].

## 3. Return on investment

Inputs: 50 CI runs a week, 30% failing, 20 minutes to triage each, 100 ILS an hour [all testimony]. Target: 10
minutes [testimony: set by the author, not measured]. Build: about 40 hours, so about 4,000 ILS [testimony, inference].
Labelling about 100 real failures at 10 minutes each: about 17 hours, about 1,700 ILS [testimony, inference].

```
failures_per_week     = 50 x 0.30                       = 15
hours_saved_per_week  = 15 x (20 - 10) / 60             = 2.5
labor_saving_per_week = 2.5 x 100 ILS                   = 250 ILS
model_cost_per_week   <= 15 x 1.0 x $0.017              = $0.25
payback_weeks         = build_cost / (labor_saving - model_cost - other_running_costs)
```

| Share of the target reached | Saving per week | Saving per year | Payback, build only (4,000 ILS) | Payback, build and labelling (about 5,700 ILS) |
|---|---|---|---|---|
| 100% (10 min saved) | 250 ILS | 13,000 ILS | 16 weeks | 23 weeks |
| 50% (5 min) | 125 ILS | 6,500 ILS | 32 weeks | 45 weeks |
| 25% (2.5 min) | 62.5 ILS | 3,250 ILS | 64 weeks | 91 weeks |

All results are [inference]: 52 weeks a year, payback is the one-off cost over the weekly saving, and the model and
AWS costs are left out. The model cost is at most about 1% of the saving at the full target and about 4% at a quarter
of it, at any rate below 10 ILS per dollar. The agent mode raises it, but it would take more than about $25 a week,
100 times the bound above, to cancel a 250 ILS saving [inference]. None of the time saving is measured yet; it needs a
pilot. Fewer interruptions and faster feedback to developers: [no data].

## 4. KPIs

| KPI | Baseline | Target | How measured |
|---|---|---|---|
| Time to diagnosis per failed build | 20 min [testimony] | 10 min [testimony: set by the author] | Slack alert to the engineer's recorded verdict. Needs the verdict button (not built); sampled by hand in a pilot. |
| Accuracy (precision and recall per category) | [no data] on real failures; 12 of 12 planted [evidence] | At least 80% on real versus flaky [testimony: set by the author] | `python -m triage eval` on at least 100 labelled real failures |
| Diagnoses accepted unchanged | [no data] | About 70% [testimony: set by the author] | Verdict button: accepted, corrected or rejected |
| Wrong at high confidence | [no data]; 0 of 12 planted [evidence] | Under 5% [testimony: set by the author] | Verdict button |
| Cost per diagnosis | $0.017 per single call [evidence] | At most $0.05 [testimony: set by the author] | Token counts in each record times the price. Must be re-measured for `agent` mode. |

The owner of every KPI is the company. Four of the five need the engineer's verdict, which is the first thing to
build for a pilot.
