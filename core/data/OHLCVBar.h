#pragma once
#include <string>

struct OHLCVBar {
    std::string date;
    double open;
    double high;
    double low;
    double close;
    double volume;
};
