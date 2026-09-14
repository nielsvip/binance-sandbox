# Add to ~/.zshrc to make 11436 the default muse endpoint (smart router)
# Bundling: S1 for memory/compute → Cloud before Mac risks crash → Mac last
# Keep conversations alive: router is stateless, Muse keeps history client-side

# Use smart router by default (S1 first)
alias muse-smart='muse --base-url http://localhost:11436/v1 --model muse-glimmer:latest'
# Keep original cloud as muse-cloud
alias muse-cloud='muse'
# Default to smart (uncomment to make 11436 the default):
# alias muse='muse --base-url http://localhost:11436/v1 --model muse-glimmer:latest'

# To force a backend:
# muse --base-url http://localhost:11436/v1 --model muse-glimmer:latest -H "X-Force-Backend: cloud" "prompt"
# muse --base-url http://localhost:11436/v1 --model muse-glimmer:latest -H "X-Force-Backend: s1" "prompt"
# muse --base-url http://localhost:11436/v1 --model muse-glimmer:latest -H "X-Force-Backend: mac" "prompt"

# Start everything:
# ~/binance/start_smart_muse.sh
# Stop to free RAM:
# ~/binance/stop_smart_muse.sh
