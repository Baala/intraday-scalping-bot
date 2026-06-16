#pragma once

// For MES futures: shares = number of contracts (always 1 at $1,000 capital)
// pointValue ($5) is applied in Python layer; C++ computes raw quantity only
struct TradeParams {
    double shares;       // contracts for futures
    double stopLoss;
    double takeProfit;
    double positionValue;
    double maxLossAmount;
};

class RiskManager {
public:
    RiskManager(double riskPercent   = 1.0,
                double stopLossPct   = 2.0,
                double atrMultiplier = 2.0,
                double rewardRatio   = 2.0);

    TradeParams calculate(double capital, double entryPrice,
                          double atrValue = 0.0) const;

    double riskPercent()   const { return riskPercent_;   }
    double stopLossPct()   const { return stopLossPct_;   }
    double atrMultiplier() const { return atrMultiplier_; }
    double rewardRatio()   const { return rewardRatio_;   }

private:
    double riskPercent_;
    double stopLossPct_;
    double atrMultiplier_;
    double rewardRatio_;
};
