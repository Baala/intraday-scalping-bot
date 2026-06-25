#pragma once
#include <cmath>

struct TradeParams {
    int    contracts;
    double stopLoss;
    double takeProfit;
    double maxRiskUsd;
};

class RiskManager {
public:
    RiskManager(double riskPercent  = 1.0,
                double slPoints     = 4.0,
                double pointValue   = 5.0,
                double rewardRatio  = 2.0,
                double atrMultiplier = 2.0);

    TradeParams calculate(double capital, double entryPrice,
                          double atrValue = 0.0) const;

    double riskPercent()   const { return riskPercent_;   }
    double slPoints()      const { return slPoints_;       }
    double pointValue()    const { return pointValue_;     }
    double rewardRatio()   const { return rewardRatio_;    }
    double atrMultiplier() const { return atrMultiplier_;  }

private:
    double riskPercent_;
    double slPoints_;
    double pointValue_;
    double rewardRatio_;
    double atrMultiplier_;
};
