# Setup after a WSL restart: local LFM models at 131,072 context

This file lives at `~/prima/swarm/deploy/SETUP_AFTER_REBOOT.md`. Do the steps in order.

## 1. Give WSL more memory (on Windows; `.wslconfig` doesn't exist yet)

WSL currently gets 7.5 GB of the 15.4 GB host. LFM2.5's experts and KV cache need about 6 GB of RAM, so raise the limit to 12 GB.

**Option A, PowerShell on Windows** (creates the file):
```powershell
Set-Content -Path "$env:USERPROFILE\.wslconfig" -Value "[wsl2]`nmemory=12GB`nswap=4GB"
wsl --shutdown
```

**Option B, by hand:** create `C:\Users\sebas\.wslconfig` (plain text, no `.txt` extension) containing:
```
[wsl2]
memory=12GB
swap=4GB
```
Then run `wsl --shutdown` in PowerShell or cmd. **This stops everything running in WSL.**

A ready-made copy is in this folder: `deploy/wslconfig.example`. From WSL, before shutting down:
`cp ~/prima/swarm/deploy/wslconfig.example /mnt/c/Users/sebas/.wslconfig`

Reopen WSL, then check:
```sh
free -g        # "Mem: total" should show about 11-12
```

## 2. Install both LFM servers (needs your sudo password)

```sh
cd ~/prima/swarm
sudo deploy/install_llama_services.sh
```

What it does:

1. Backs up the current Hermes unit to `/etc/systemd/system/llama-server.service.bak`. It never overwrites an existing backup.
2. Installs the settings to `/etc/default/llama-swarm` (source: `deploy/llama-swarm.env`), plus both units.
3. Stops Hermes LFM2.5, starts **LFM3-1.7B first** (port 18081, entirely on the CUDA GPU), then **LFM2.5-8B** (port 18080, attention and KV cache on CUDA, MoE experts on the CPU). Both use **131,072 context**.
4. Waits for both to answer, then prints VRAM and RAM use.

Both services start automatically at boot.

## 3. Check

```sh
./swarm.sh server status                    # local:lfm2.5-8b and local:lfm3-1.7b must both be "up"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
curl -s localhost:18081/props | python3 -c "import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])"   # expect 131072
curl -s localhost:18080/props | python3 -c "import json,sys; print(json.load(sys.stdin)['default_generation_settings']['n_ctx'])"   # expect 128000 (llama.cpp caps LFM2.5 at its trained length)
./swarm.sh probe local:lfm2.5-8b local:lfm3-1.7b   # each makes one tool call
```
Then ask Claude to run the full verification: context, memory, tool calls, temperature-0 determinism and speed.

## If something fails

| Symptom | Fix |
| --- | --- |
| LFM3 doesn't come up (out of VRAM) | Something else holds the GPU. Run `nvidia-smi` and stop it, then `sudo systemctl restart llama-server-lfm3 llama-server` |
| LFM2.5 is killed or very slow (RAM) | Do step 1, or set `KV_TYPE=q4_0` in `/etc/default/llama-swarm` (halves both KV caches), then restart the services |
| Logs | `journalctl -u llama-server-lfm3 -n 50 --no-pager` and `journalctl -u llama-server -n 50 --no-pager` |
| Undo everything | `sudo deploy/install_llama_services.sh --rollback` restores the original Hermes unit (LFM2.5, 65,536 context, all on GPU) and removes the LFM3 service |

## Notes

- **The AMD Radeon iGPU can't be used from WSL.** Vulkan only sees `llvmpipe`, so LFM2.5's overflow goes to the CPU.
- **LFM2.5 was trained to 128,000 tokens.** Past that it's beyond its training. Episodes use about 10–40k tokens, so this is fine. A YaRN option is in `/etc/default/llama-swarm` (`LFM25_ROPE`).
- **To tune,** edit `/etc/default/llama-swarm`, then `sudo systemctl restart llama-server-lfm3 llama-server`.
- **Ablation 05 is paused.** Resume it after setup with `nohup scripts/ablations/run_all.sh 05 > logs/ablations/run_05.log 2>&1 &`.
