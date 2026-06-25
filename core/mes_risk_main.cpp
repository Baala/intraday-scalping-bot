#include <iostream>
#include <string>
#include <cstdlib>
#include "nlohmann/json.hpp"
#include "risk/RiskManager.h"

using json = nlohmann::json;

static double get_arg(int argc, char* argv[], const std::string& flag, bool required = true) {
    for (int i = 1; i < argc - 1; ++i) {
        if (argv[i] == flag) {
            return std::stod(argv[i + 1]);
        }
    }
    if (required) {
        std::cerr << "Missing required argument: " << flag << "\n";
        std::exit(1);
    }
    return 0.0;
}

int main(int argc, char* argv[]) {
    try {
        double entry      = get_arg(argc, argv, "--entry");
        double capital    = get_arg(argc, argv, "--capital");
        double riskPct    = get_arg(argc, argv, "--risk-pct");
        double slPoints   = get_arg(argc, argv, "--sl-points");
        double pointValue = get_arg(argc, argv, "--point-value");
        double rr         = get_arg(argc, argv, "--rr");
        double atr        = get_arg(argc, argv, "--atr", false);  // optional

        RiskManager rm(riskPct, slPoints, pointValue, rr);
        TradeParams p = rm.calculate(capital, entry, atr);

        if (p.contracts < 1) {
            json err = {{"error", "contracts < 1 — insufficient capital for this risk/stop combination"}};
            std::cout << err.dump() << "\n";
            return 1;
        }

        json out = {
            {"contracts",  p.contracts},
            {"stop_loss",  p.stopLoss},
            {"take_profit", p.takeProfit},
            {"max_risk_usd", p.maxRiskUsd}
        };
        std::cout << out.dump() << "\n";
        return 0;

    } catch (const std::exception& e) {
        json err = {{"error", std::string(e.what())}};
        std::cout << err.dump() << "\n";
        return 1;
    }
}
