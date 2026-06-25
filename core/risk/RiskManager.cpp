#include "RiskManager.h"
#include <cmath>

RiskManager::RiskManager(double riskPercent, double slPoints,
                         double pointValue, double rewardRatio,
                         double atrMultiplier)
    : riskPercent_(riskPercent)
    , slPoints_(slPoints)
    , pointValue_(pointValue)
    , rewardRatio_(rewardRatio)
    , atrMultiplier_(atrMultiplier)
{}

TradeParams RiskManager::calculate(double capital, double entryPrice,
                                   double atrValue) const {
    TradeParams p;

    double riskPoints;
    if (atrValue > 0.0) {
        riskPoints  = atrValue * atrMultiplier_;
        p.stopLoss  = entryPrice - riskPoints;
    } else {
        riskPoints  = slPoints_;
        p.stopLoss  = entryPrice - slPoints_;
    }

    double riskPerContract = riskPoints * pointValue_;
    p.maxRiskUsd = capital * (riskPercent_ / 100.0);
    p.contracts  = (riskPerContract > 0.0)
                   ? static_cast<int>(std::floor(p.maxRiskUsd / riskPerContract))
                   : 0;
    p.takeProfit = entryPrice + riskPoints * rewardRatio_;

    return p;
}
