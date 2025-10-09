# ML Data Strategy: Current vs Optimal

**Date:** October 5, 2025

---

## Current Data Usage (Suboptimal)

### What We're Using Now

```
2023-01-01 ──────────────────────── 2024-06-30 │ 2024-07-01 ──────── 2025-08-15
                TRAINING (18 months)            │      UNUSED (14 months!)
                     ↓                          │            ↓
              80% train / 20% val               │    Sitting idle, wasted
              (random/chron split)               │
```

**Problems:**
1. ❌ Validation set is from same time period (in-sample)
2. ❌ 14 months of recent data completely unused
3. ❌ No true out-of-sample testing
4. ❌ Models haven't seen 2024-2025 market conditions
5. ❌ Can't measure real-world performance

---

## What the Data SHOULD Be Used For

### Jul 2024 - Aug 2025 (Most Recent 14 Months)

This is your **GOLD** - the most recent, relevant data. It should be used for:

**Option A: Walk-Forward Validation (Test Model Quality)**
```
Train:  2023-01-01 ──────────────── 2024-06-30
                                         │
Test:                              2024-07-01 ──────── 2025-08-15
                                        ↓
                            TRUE out-of-sample test
                            Unseen future data
                            Honest performance metrics
```

**Purpose:** 
- Measure true generalization on unseen future data
- Validate models work on recent market conditions
- Get honest MAE/RMSE for production expectations

**Option B: Full Retrain (Maximize Performance)**
```
Train:  2023-01-01 ─────────────────────────────────── 2025-08-15
                        ALL 32 months of data
                        78% more training samples!
```

**Purpose:**
- Maximize learning from all available data
- Models learn recent 2024-2025 volatility regime
- Better calibrated to current market environment
- More data = better generalization (if model not overfitting)

---

## Recommended Two-Step Strategy

### Step 1: Walk-Forward Validation (THIS FIRST)

**Run:** `python run_walk_forward_validation.py`

**What it does:**
1. Loads current models (trained on 2023-2024 H1)
2. Tests on held-out Jul 2024 - Aug 2025 data
3. Reports true out-of-sample MAE/RMSE
4. Compares to in-sample validation metrics

**Expected outcomes:**
- If test MAE similar to validation MAE → Model generalizes well ✅
- If test MAE much worse → Model overfitting ⚠️
- If test MAE better → Lucky or train set was harder ✅

**Why this matters:**
This tells you if your models are **actually good** or just memorizing training data.

### Step 2: Full Retrain (AFTER VALIDATION)

**Run:** `python run_full_retrain_2023_2025.py --n-trials 50`

**What it does:**
1. Retrains models on ALL 32 months (2023-2025)
2. Rebuilds bias curves with full historical data
3. Optimizes hyperparameters on larger dataset
4. Saves new production models

**Expected improvements:**
- 78% more training samples (helps especially for rare events)
- Models learn recent 2024-2025 market regime
- Better calibrated to current volatility environment
- T-1, T-2, T-3 models likely improve most (need more samples)

---

## Data Breakdown

### Current Available Data

```
Period               Months  Events   Purpose (Current)    Purpose (Optimal)
─────────────────────────────────────────────────────────────────────────────
2023-01-01 to        18      ~9,000   Training (80%)       Training + Val
2024-06-30                            Validation (20%)     

2024-07-01 to        14      ~8,000   UNUSED!             Walk-forward test
2025-08-15                                                 OR full retrain

Total                32      ~17,000  Using 18 months     Use ALL 32 months
```

### Why 80/20 Split is Suboptimal

**Traditional ML:** Use 80/20 split when data is i.i.d. (independent, identically distributed)

**Time Series ML:** Data is NOT i.i.d. Future is different from past!

**Better approach:**
1. Use FUTURE data for testing (walk-forward)
2. OR use ALL data for training (no validation split)
3. Monitor production performance as validation

---

## Detailed Recommendations

### Recommendation 1: Run Walk-Forward Validation NOW

```bash
cd apps/ml
python run_walk_forward_validation.py
```

**What you'll learn:**
- True MAE on unseen 2024-2025 data
- Whether models overfit or generalize
- Which horizons (T-1, T-2, etc.) perform best on recent data
- If 2024-2025 market regime is different from 2023-2024

**Example output:**
```
T-1:  Val MAE: 2.59% → Test MAE: 3.12% (20% worse, acceptable)
T-2:  Val MAE: 0.25% → Test MAE: 0.31% (24% worse, acceptable)
T-7:  Val MAE: 0.61% → Test MAE: 0.55% (10% better, great!)
```

### Recommendation 2: Full Retrain on All Data

```bash
cd apps/ml
python run_full_retrain_2023_2025.py --n-trials 50
```

**Benefits:**
- 32 months vs 18 months = 78% more data
- ~17,000 earnings events vs ~9,000
- Models learn 2024-2025 market conditions
- Better tail behavior (rare large moves)

**Risks:**
- No held-out test set (must monitor production)
- Slightly longer training time (~60 min)

**Mitigation:**
- Run walk-forward validation first to baseline
- Monitor production MAE vs predictions daily
- Retrain monthly to stay current

---

## Expected Performance Improvements

### T-2 Model (Currently Best: 0.25% MAE)
- **Current:** 4,649 samples (2023-2024 H1)
- **After retrain:** ~8,000 samples (+72%)
- **Expected MAE:** 0.15-0.20% (20-33% improvement)

### T-1 Model (Currently Worst: 2.59% MAE)
- **Current:** 3,788 samples (smallest dataset)
- **After retrain:** ~7,000 samples (+85%)
- **Expected MAE:** 1.8-2.2% (15-30% improvement)
- **Why:** T-1 needs more data to learn volatile day-before patterns

### T-7, T-14, T-21 Models
- **Current:** ~4,800 samples each
- **After retrain:** ~8,500 samples (+77%)
- **Expected MAE:** 10-25% improvement
- **Why:** Larger sample size reduces overfitting

---

## Production Strategy

### Ongoing Data Management

**Monthly Retraining:**
```
Month 1: Train on 2023-01 to 2025-08
Month 2: Train on 2023-01 to 2025-09 (1 more month)
Month 3: Train on 2023-01 to 2025-10 (2 more months)
```

**Expanding Window Approach:**
- Always use ALL available historical data
- Models get better over time with more samples
- Drop data older than 3 years to stay relevant

**Production Monitoring:**
```python
# Daily monitoring
actual_mae = calculate_mae(predictions, realized_moves)

if actual_mae > expected_mae * 1.5:
    alert("Model degrading - consider retrain")
```

---

## Summary: What to Do Right Now

### Priority 1: Validate Current Models
```bash
python run_walk_forward_validation.py
```
**Time:** 5 minutes  
**Purpose:** Know if current models actually work on unseen data

### Priority 2: Full Retrain
```bash
python run_full_retrain_2023_2025.py --n-trials 50
```
**Time:** 30-60 minutes  
**Purpose:** Maximize performance with all available data

### Priority 3: Deploy New Models
```bash
# Models auto-saved to data/models/
# Restart backend to load new models
pkill -f uvicorn
cd apps/backend && uvicorn main:app --reload
```

### Priority 4: Monitor Production
- Track daily MAE vs predictions
- Compare to validation metrics
- Retrain monthly or when MAE degrades >20%

---

## Questions Answered

**Q: What data is being used for validation?**  
A: Currently 20% of 2023-2024 H1 data (in-sample, not ideal)

**Q: What data is being used for test?**  
A: None! Jul 2024 - Aug 2025 is unused (should be walk-forward test)

**Q: Is validation using more recent data?**  
A: No, validation is random/chronological split within 2023-2024 H1

**Q: What is Jul 2024 - Aug 2025 data used for?**  
A: Nothing currently! That's the problem we're fixing.

**Q: Should I use it for testing or training?**  
A: BOTH!
1. First: Test current models on it (walk-forward validation)
2. Then: Retrain including it (full 2023-2025 training)

---

**Next Steps:**
1. ✅ Run walk-forward validation (5 min)
2. ✅ Review test set performance
3. ✅ Run full retrain (60 min)
4. ✅ Deploy new models
5. ✅ Monitor production MAE daily

**Expected Outcome:**
- Know true model performance on recent data
- Maximize learning with 78% more training data
- Better calibrated models for current market
- Production-ready 2025 forecasting system
