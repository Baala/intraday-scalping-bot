#include "BacktestEngine.h"
#include "risk/RiskManager.h"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <sstream>

BacktestEngine::BacktestEngine(BacktestConfig cfg) : cfg_(std::move(cfg)) {}

// ── Helpers ──────────────────────────────────────────────────────────────────

std::string BacktestEngine::barDate(const std::string& dt) const {
    return dt.size() >= 10 ? dt.substr(0, 10) : dt;
}

std::string BacktestEngine::barTime(const std::string& dt) const {
    // dt format: "YYYYMMDD HH:MM:SS" or "YYYY-MM-DD HH:MM:SS"
    auto pos = dt.find(' ');
    return (pos != std::string::npos) ? dt.substr(pos + 1, 5) : "00:00";
}

bool BacktestEngine::isMarketHours(const std::string& timeStr) const {
    return timeStr >= cfg_.marketOpen && timeStr < cfg_.marketClose;
}

// ── Indicator updates ─────────────────────────────────────────────────────────

void BacktestEngine::updateEma(double close) {
    warmupCloses_.push_back(close);

    if (!emaReady_) {
        if ((int)warmupCloses_.size() == cfg_.emaSlow) {
            double sumF = 0.0, sumS = 0.0;
            int sz = (int)warmupCloses_.size();
            for (int i = sz - cfg_.emaFast; i < sz; ++i) sumF += warmupCloses_[i];
            for (double v : warmupCloses_) sumS += v;
            emaFast_ = sumF / cfg_.emaFast;
            emaSlow_ = sumS / cfg_.emaSlow;
            emaReady_ = true;
        }
        return;
    }

    double mf = 2.0 / (cfg_.emaFast + 1);
    double ms = 2.0 / (cfg_.emaSlow + 1);
    prevEmaFast_ = emaFast_;
    prevEmaSlow_ = emaSlow_;
    emaFast_ = emaFast_ + mf * (close - emaFast_);
    emaSlow_ = emaSlow_ + ms * (close - emaSlow_);
}

double BacktestEngine::updateVwap(const OHLCVBar& bar) {
    std::string date = barDate(bar.date);
    std::string t    = barTime(bar.date);
    if (t < cfg_.vwapAnchor) return 0.0;
    if (vwapDate_ != date) {
        cumTpv_ = 0.0; cumVol_ = 0.0; vwapDate_ = date;
    }
    double tp = (bar.high + bar.low + bar.close) / 3.0;
    cumTpv_ += tp * bar.volume;
    cumVol_ += bar.volume;
    return cumVol_ > 0.0 ? cumTpv_ / cumVol_ : 0.0;
}

double BacktestEngine::updateAdx(const OHLCVBar& bar) {
    if (!prevSet_) {
        prevHigh_ = bar.high; prevLow_ = bar.low; prevClose_ = bar.close;
        prevSet_ = true;
        return 0.0;
    }
    double up   = bar.high - prevHigh_;
    double down = prevLow_  - bar.low;
    double dmP  = (up > down && up > 0)   ? up   : 0.0;
    double dmM  = (down > up && down > 0) ? down : 0.0;

    double tr = std::max({bar.high - bar.low,
                          std::abs(bar.high - prevClose_),
                          std::abs(bar.low  - prevClose_)});

    prevHigh_ = bar.high; prevLow_ = bar.low; prevClose_ = bar.close;

    double mult = 2.0 / (cfg_.atrPeriod + 1);
    auto ema_update = [&](double prev, double val) {
        return adxReady_ ? prev + mult * (val - prev) : val;
    };
    dmPlusEma_  = ema_update(dmPlusEma_,  dmP);
    dmMinusEma_ = ema_update(dmMinusEma_, dmM);
    trEma_      = ema_update(trEma_,      tr);
    adxReady_   = true;

    if (trEma_ == 0.0) return 0.0;
    double diP = 100.0 * dmPlusEma_  / trEma_;
    double diM = 100.0 * dmMinusEma_ / trEma_;
    double sum = diP + diM;
    double dx  = (sum > 0.0) ? 100.0 * std::abs(diP - diM) / sum : 0.0;
    dxEma_ = adxReady_ ? dxEma_ + mult * (dx - dxEma_) : dx;
    return dxEma_;
}

std::pair<double,double> BacktestEngine::computeAtr(const OHLCVBar& bar) {
    if (trs_.empty()) return {0.0, 0.0};
    double current_tr = trs_.back();
    if ((int)trs_.size() < cfg_.atrPeriod) return {0.0, current_tr};
    int sz = (int)trs_.size();
    double sum = 0.0;
    for (int i = sz - cfg_.atrPeriod; i < sz; ++i) sum += trs_[i];
    return {sum / cfg_.atrPeriod, current_tr};
}

bool BacktestEngine::volumeOk(double vol) const {
    if ((int)vols_.size() < 20) return true;
    double sum = 0.0;
    int sz = (int)vols_.size();
    for (int i = sz - 20; i < sz; ++i) sum += vols_[i];
    double avg = sum / 20.0;
    return vol >= avg * (cfg_.volumePct / 100.0);
}

// ── Main run ──────────────────────────────────────────────────────────────────

BacktestSummary BacktestEngine::run(const std::vector<OHLCVBar>& bars) {
    trades_.clear();
    resetIndicators();

    RiskManager rm(cfg_.riskPct, cfg_.slPoints, cfg_.pointValue, cfg_.capital / cfg_.capital);
    // contracts = floor(capital * riskPct/100 / (slPoints * pointValue))
    int contracts = static_cast<int>(std::floor(
        (cfg_.capital * cfg_.riskPct / 100.0) / (cfg_.slPoints * cfg_.pointValue)));
    if (contracts < 1) contracts = 1;

    bool   inTrade    = false;
    double entryPrice = 0.0, sl = 0.0, tp = 0.0;
    std::string entryTime;
    double prevFast = 0.0, prevSlow = 0.0;
    bool   prevEmaSet = false;

    for (size_t i = 0; i < bars.size(); ++i) {
        const auto& bar = bars[i];

        // Update indicators
        updateEma(bar.close);

        // Build TR for this bar
        if (i > 0) {
            double tr = std::max({bar.high - bar.low,
                                  std::abs(bar.high - bars[i-1].close),
                                  std::abs(bar.low  - bars[i-1].close)});
            trs_.push_back(tr);
        }
        vols_.push_back(bar.volume);

        double vwap = updateVwap(bar);
        double adx  = updateAdx(bar);
        auto [atr, current_tr] = computeAtr(bar);

        if (!emaReady_) continue;

        std::string t = barTime(bar.date);

        // Check open trade first
        if (inTrade) {
            if (bar.low <= sl && bar.high >= tp) {
                // Both hit — SL is conservative
                double fillPrice = sl - cfg_.exitSlippage;
                double pnl = (fillPrice - entryPrice) * cfg_.pointValue * contracts - cfg_.commission * contracts;
                trades_.push_back({entryTime, bar.date, entryPrice, fillPrice, pnl, "SL", contracts});
                inTrade = false;
            } else if (bar.low <= sl) {
                double fillPrice = sl - cfg_.exitSlippage;
                double pnl = (fillPrice - entryPrice) * cfg_.pointValue * contracts - cfg_.commission * contracts;
                trades_.push_back({entryTime, bar.date, entryPrice, fillPrice, pnl, "SL", contracts});
                inTrade = false;
            } else if (bar.high >= tp) {
                double pnl = (tp - entryPrice) * cfg_.pointValue * contracts - cfg_.commission * contracts;
                trades_.push_back({entryTime, bar.date, entryPrice, tp, pnl, "TP", contracts});
                inTrade = false;
            }
            // If still in trade, do not look for entry
            if (inTrade) { prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true; continue; }
        }

        // Entry conditions
        if (!isMarketHours(t)) { prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true; continue; }
        if (adx < cfg_.adxMin && adxReady_) { prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true; continue; }
        if (atr > 0.0 && current_tr > cfg_.atrSpikeMult * atr) { prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true; continue; }
        if (!volumeOk(bar.volume)) { prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true; continue; }
        if (vwap > 0.0 && bar.close < vwap) { prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true; continue; }

        // Crossover detection
        if (prevEmaSet && prevFast <= prevSlow && emaFast_ > emaSlow_) {
            // BUY signal
            entryPrice = bar.close + cfg_.entrySlippage;
            sl = entryPrice - cfg_.slPoints;
            tp = entryPrice + cfg_.tpPoints;
            entryTime = bar.date;
            inTrade = true;
        }

        prevFast = emaFast_; prevSlow = emaSlow_; prevEmaSet = true;
    }

    // Summarise
    BacktestSummary s{};
    s.totalTrades = (int)trades_.size();
    double peak = 0.0, equity = 0.0, sumWin = 0.0, sumLoss = 0.0;
    s.bestTrade  = trades_.empty() ? 0.0 : trades_[0].pnl;
    s.worstTrade = trades_.empty() ? 0.0 : trades_[0].pnl;

    for (auto& tr : trades_) {
        equity += tr.pnl;
        peak = std::max(peak, equity);
        s.maxDrawdown = std::max(s.maxDrawdown, peak - equity);
        s.bestTrade   = std::max(s.bestTrade,   tr.pnl);
        s.worstTrade  = std::min(s.worstTrade,  tr.pnl);
        if (tr.exitReason == "TP") { ++s.wins; sumWin += tr.pnl; }
        else                       { ++s.losses; sumLoss += tr.pnl; }
    }
    s.losses   = s.totalTrades - s.wins;
    s.totalPnl = equity;
    s.winRate  = s.totalTrades > 0 ? (double)s.wins / s.totalTrades : 0.0;
    s.avgWin   = s.wins   > 0 ? sumWin  / s.wins   : 0.0;
    s.avgLoss  = s.losses > 0 ? sumLoss / s.losses : 0.0;
    return s;
}

void BacktestEngine::resetIndicators() {
    emaFast_ = emaSlow_ = prevEmaFast_ = prevEmaSlow_ = 0.0;
    emaReady_ = false;
    cumTpv_ = cumVol_ = 0.0; vwapDate_.clear();
    prevHigh_ = prevLow_ = prevClose_ = 0.0; prevSet_ = false;
    dmPlusEma_ = dmMinusEma_ = trEma_ = dxEma_ = 0.0; adxReady_ = false;
    trs_.clear(); vols_.clear(); warmupCloses_.clear();
}
