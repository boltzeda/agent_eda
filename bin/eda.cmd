@echo off
set "AGENT_EDA_SCRIPT=%~dp0eda"
py -3 "%AGENT_EDA_SCRIPT%" %*
