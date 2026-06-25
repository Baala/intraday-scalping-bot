#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include "backtest/BacktestEngine.h"
#include "data/OHLCVBar.h"

static std::string trim(std::string s) {
    while (!s.empty() && (s.back() == '\r' || s.back() == '\n' || s.back() == ' '))
        s.pop_back();
    return s;
}

static std::vector<OHLCVBar> loadCsv(const std::string& path) {
    std::ifstream f(path);
    if (!f) { std::cerr << "Cannot open: " << path << "\n"; std::exit(1); }

    std::vector<OHLCVBar> bars;
    std::string line;
    std::getline(f, line);  // skip header

    while (std::getline(f, line)) {
        line = trim(line);
        if (line.empty()) continue;
        std::istringstream ss(line);
        OHLCVBar b;
        std::string tok;
        std::getline(ss, b.date,   ',');
        std::getline(ss, tok,      ','); b.open   = std::stod(tok);
        std::getline(ss, tok,      ','); b.high   = std::stod(tok);
        std::getline(ss, tok,      ','); b.low    = std::stod(tok);
        std::getline(ss, tok,      ','); b.close  = std::stod(tok);
        std::getline(ss, tok,      ','); b.volume = std::stod(tok);
        bars.push_back(b);
    }
    return bars;
}

static std::string get_str(int argc, char* argv[], const std::string& flag, std::string def) {
    for (int i = 1; i < argc - 1; ++i)
        if (argv[i] == flag) return argv[i + 1];
    return def;
}
static double get_dbl(int argc, char* argv[], const std::string& flag, double def) {
    for (int i = 1; i < argc - 1; ++i)
        if (std::string(argv[i]) == flag) return std::stod(argv[i + 1]);
    return def;
}

int main(int argc, char* argv[]) {
    std::string csvPath = get_str(argc, argv, "--csv", "data/mes_15min.csv");

    auto bars = loadCsv(csvPath);
    std::cout << "Loaded " << bars.size() << " bars from " << csvPath << "\n\n";

    BacktestConfig cfg;
    cfg.slPoints    = get_dbl(argc, argv, "--sl-points", cfg.slPoints);
    cfg.tpPoints    = cfg.slPoints * get_dbl(argc, argv, "--rr", 2.0);
    cfg.emaFast     = (int)get_dbl(argc, argv, "--ema-fast", cfg.emaFast);
    cfg.emaSlow     = (int)get_dbl(argc, argv, "--ema-slow", cfg.emaSlow);
    cfg.adxMin      = get_dbl(argc, argv, "--adx-min",  cfg.adxMin);

    std::cout << "Config: sl=" << cfg.slPoints << "pts  tp=" << cfg.tpPoints
              << "pts  EMA(" << cfg.emaFast << "/" << cfg.emaSlow
              << ")  ADX>=" << cfg.adxMin << "\n\n";

    BacktestEngine engine(cfg);
    auto s = engine.run(bars);

    std::cout << "=== Backtest Summary ===\n";
    std::cout << "Total trades : " << s.totalTrades << "\n";
    std::cout << "Wins         : " << s.wins << "  Losses: " << s.losses << "\n";
    std::cout << "Win rate     : " << (s.winRate * 100.0) << "%\n";
    std::cout << "Total P&L    : $" << s.totalPnl << "\n";
    std::cout << "Avg win      : $" << s.avgWin  << "\n";
    std::cout << "Avg loss     : $" << s.avgLoss << "\n";
    std::cout << "Best trade   : $" << s.bestTrade  << "\n";
    std::cout << "Worst trade  : $" << s.worstTrade << "\n";
    std::cout << "Max drawdown : $" << s.maxDrawdown << "\n";

    // Write trade log CSV
    std::string outPath = "data/backtest_results.csv";
    std::ofstream out(outPath);
    out << "entry_time,exit_time,entry_price,exit_price,contracts,pnl,exit_reason\n";
    for (auto& tr : engine.trades()) {
        out << tr.entryTime << "," << tr.exitTime << ","
            << tr.entryPrice << "," << tr.exitPrice << ","
            << tr.contracts << "," << tr.pnl << "," << tr.exitReason << "\n";
    }
    std::cout << "\nTrade log saved to " << outPath << "\n";
    return 0;
}
