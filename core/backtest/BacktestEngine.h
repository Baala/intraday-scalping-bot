#pragma once
#include <string>
#include <vector>
#include "data/OHLCVBar.h"

struct TradeRecord {
    std::string entryTime;
    std::string exitTime;
    double      entryPrice;
    double      exitPrice;
    double      pnl;
    std::string exitReason;  // "TP" | "SL"
    int         contracts;
};

struct BacktestSummary {
    int    totalTrades;
    int    wins;
    int    losses;
    double winRate;
    double totalPnl;
    double maxDrawdown;
    double avgWin;
    double avgLoss;
    double bestTrade;
    double worstTrade;
};

struct BacktestConfig {
    double capital        = 5000.0;
    double riskPct        = 1.0;
    double slPoints       = 4.0;
    double tpPoints       = 8.0;
    double pointValue     = 5.0;
    double commission     = 1.70;   // round-trip per contract
    double entrySlippage  = 0.25;   // points
    double exitSlippage   = 0.25;   // points (SL only; TP is limit)
    int    emaFast        = 5;
    int    emaSlow        = 20;
    double adxMin         = 20.0;
    double atrSpikeMult   = 2.0;
    int    atrPeriod      = 14;
    int    volumePct      = 50;
    std::string marketOpen  = "09:45";
    std::string marketClose = "15:30";
    std::string vwapAnchor  = "09:30";
};

class BacktestEngine {
public:
    explicit BacktestEngine(BacktestConfig cfg = {});

    BacktestSummary run(const std::vector<OHLCVBar>& bars);
    const std::vector<TradeRecord>& trades() const { return trades_; }

private:
    BacktestConfig cfg_;
    std::vector<TradeRecord> trades_;

    // Running indicator state
    double emaFast_  = 0.0;
    double emaSlow_  = 0.0;
    bool   emaReady_ = false;

    double prevEmaFast_ = 0.0;
    double prevEmaSlow_ = 0.0;

    double cumTpv_ = 0.0, cumVol_ = 0.0;
    std::string vwapDate_;

    // ADX state
    double prevHigh_ = 0.0, prevLow_ = 0.0, prevClose_ = 0.0;
    bool   prevSet_  = false;
    double dmPlusEma_ = 0.0, dmMinusEma_ = 0.0, trEma_ = 0.0, dxEma_ = 0.0;
    bool   adxReady_ = false;

    // ATR
    std::vector<double> trs_;

    // Volume
    std::vector<double> vols_;

    // Warmup
    std::vector<double> warmupCloses_;

    void   resetIndicators();
    void   updateEma(double close);
    double updateVwap(const OHLCVBar& bar);
    double updateAdx(const OHLCVBar& bar);
    std::pair<double,double> computeAtr(const OHLCVBar& bar);
    bool   volumeOk(double vol) const;
    bool   isMarketHours(const std::string& timeStr) const;
    std::string barDate(const std::string& dt) const;
    std::string barTime(const std::string& dt) const;
};
