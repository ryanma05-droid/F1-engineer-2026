#!/bin/bash
# Race Engineer.app's executable: opens the engineer in a Terminal window, because it's a text
# app (lap lines, radio calls, first-run questions). The real program is in Resources/engine/.
DIR="$(cd "$(dirname "$0")/../Resources/engine" && pwd)"
exec open -a Terminal "$DIR/RaceEngineer"
