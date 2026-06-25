cmake_minimum_required(VERSION 3.16)
project(mes_scalping CXX)
set(CMAKE_CXX_STANDARD 17)

if(MINGW)
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static-libgcc -static-libstdc++ -static")
endif()

include_directories(include core)

add_executable(mes_risk
    core/mes_risk_main.cpp
    core/risk/RiskManager.cpp
)

add_executable(backtest
    core/backtest_main.cpp
    core/backtest/BacktestEngine.cpp
    core/risk/RiskManager.cpp
)
