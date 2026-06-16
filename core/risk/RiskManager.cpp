#include "RiskManager.h"
#include <cmath>

RiskManager::RiskManager(double riskPercent, double stopLossPct,
                         double atrMultiplier, double rewardRatio)
    : riskPercent_(riskPercent)
    , stopLossPct_(stopLossPct)
    , atrMultiplier_(atrMultiplier)
    , rewardRatio_(rewardRatio)
{}

TradeParams RiskManager::calculate(double capital, double entryPrice,
                                   double atrValue) const {
    TradeParams p;

    if (atrValue > 0.0)
        p.stopLoss = entryPrice - atrValue * atrMultiplier_;
    else
        p.stopLoss = entryPrice * (1.0 - stopLossPct_ / 100.0);

    double riskPerShare = entryPrice - p.stopLoss;

    p.maxLossAmount = capital * (riskPercent_ / 100.0);
    p.shares        = (riskPerShare > 0.0)
                      ? p.maxLossAmount / riskPerShare
                      : 0.0;

    p.takeProfit    = entryPrice + riskPerShare * rewardRatio_;
    p.positionValue = p.shares * entryPrice;

    return p;
}

// Commission note for MES backtesting:
// IB charges $0.85/side = $1.70 round trip per contract.
// To pass this to BacktestEngine (which takes commissionPct), convert:
//   commissionPct = (1.70 / (entryPrice * pointValue)) * 100
// where pointValue = 5.0 for MES.
// Effective RR after commission: $18.30 win / $11.70 loss = 1.56 (not 2.0).
