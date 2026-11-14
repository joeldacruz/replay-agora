# Instalação do agente em Ubuntu (22.04/24.04) — procedimento para a homologação

> **Não execute em produção sem a autorização e o plano de [HOMOLOGACAO-replayagora-srv01.md](HOMOLOGACAO-replayagora-srv01.md).** Nada aqui foi executado no `replayagora-srv01`. O procedimento **não sobrescreve** nem altera `/srv/replayagora/*` (código legado), a gravação `leitor.py`, o firmware do Arduino, DNS ou Cloudflare. Tudo fica em diretórios novos (`/opt/replayagora`, `/etc/replayagora`, `/var/lib/replayagora`).

## 0. Pré-requisitos (somente leitura)
```bash
lsb_release -ds; python3 --version            # Python >= 3.10
ffmpeg -version | head -1; ffprobe -version | head -1
df -h /var/lib /srv; free -m; nproc
systemctl list-units --type=service | grep -i replay      # serviços legados em execução (NÃO parar nesta etapa)
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null; ls /dev/dri 2>/dev/null || echo "sem GPU DRM (CPU apenas)"
```
**Verificação:** Python ≥ 3.10, FFmpeg presente, espaço livre ≥ (bitrate × `buffer_s` × câmeras) + replays. **Rollback:** nada alterado.

## 1. Usuário, diretórios e dependências
```bash
sudo apt-get update && sudo apt-get install -y ffmpeg python3-venv
sudo useradd --system --home /var/lib/replayagora --shell /usr/sbin/nologin replayagora
sudo usermod -aG dialout replayagora
sudo install -d -o replayagora -g replayagora -m 0750 /var/lib/replayagora
sudo install -d -o root -g replayagora -m 0750 /etc/replayagora
sudo install -d -o root -g root -m 0755 /opt/replayagora/agent
sudo python3 -m venv /opt/replayagora/venv && sudo /opt/replayagora/venv/bin/pip install httpx
```
**Verificação:** `id replayagora` mostra `dialout`; `/opt/replayagora/venv/bin/python -c "import httpx"`.
**Rollback:** `sudo userdel replayagora; sudo rm -rf /opt/replayagora /var/lib/replayagora /etc/replayagora` (nada fora desses caminhos foi criado).

## 2. Código do agente (cópia do repositório, versão fixa)
```bash
git clone --branch claude/hopeful-carson-upfxyr https://github.com/joeldacruz/replay-agora.git /tmp/replay-agora
cd /tmp/replay-agora && git rev-parse HEAD            # anote o commit homologado
sudo cp -r services/local-agent/replay_agent /opt/replayagora/agent/
sudo cp deploy/systemd/replayagora-agent.service /etc/systemd/system/
sudo cp deploy/udev/99-replayagora.rules /etc/udev/rules.d/
```
**Verificação:** `PYTHONPATH=/opt/replayagora/agent /opt/replayagora/venv/bin/python -m replay_agent hwinfo`.
**Rollback:** `sudo rm -rf /opt/replayagora/agent/replay_agent /etc/systemd/system/replayagora-agent.service /etc/udev/rules.d/99-replayagora.rules && sudo systemctl daemon-reload`.

## 3. Nome estável da botoeira (udev) — sem tocar na botoeira
```bash
udevadm info -a -n /dev/ttyUSB0 | grep -E 'KERNELS|idVendor|idProduct' | head     # descubra a porta USB (KERNELS)
sudoedit /etc/udev/rules.d/99-replayagora.rules                                    # ajuste KERNELS=="..."
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
ls -l /dev/replayagora/botoeira
```
O CH340 **não tem número de série**: o nome estável depende da **porta USB física**. Se o `ModemManager` estiver instalado, a regra já define `ID_MM_DEVICE_IGNORE`. **Rollback:** remover a regra e `udevadm control --reload`.

## 4. Configuração (arquivo 0600; contém credenciais de câmera)
```bash
sudo install -m 0600 -o replayagora -g replayagora /tmp/replay-agora/deploy/agent.example.json /etc/replayagora/agent.json
sudoedit /etc/replayagora/agent.json      # URLs RTSP, botoeiras, janelas; NUNCA versione este arquivo
sudo -u replayagora PYTHONPATH=/opt/replayagora/agent /opt/replayagora/venv/bin/python -m replay_agent check-config --config /etc/replayagora/agent.json
```
**Verificação:** `check-config` imprime a configuração **sem senhas** (`rtsp://***@…`); recusa permissão ≠ 0600. **Rollback:** `sudo rm /etc/replayagora/agent.json`.

**Credenciais da central** (token + chave de assinatura gerados no App da Arena → Motor local → Nova credencial; aparecem **uma vez**):
```bash
sudo install -m 0600 -o replayagora -g replayagora /dev/null /etc/replayagora/credentials.json
sudoedit /etc/replayagora/credentials.json     # {"token":"rag_...","signingKey":"..."}
```
A central deve usar **https://**; o agente só faz conexões de saída (nenhuma porta é aberta).

## 5. Modos de integração com o legado (escolher conforme a fase do plano)
* **Sombra (recomendado primeiro):** `"serial": [{"name":"leitor","mode":"log_tail","journal_unit":"<serviço do leitor.py>"}]`. Não abre a porta; o `leitor.py` segue no comando.
* **Cutover de uma quadra/botoeira:** `"mode":"serial"`. **Antes**: `sudo systemctl stop <serviço do leitor.py>` (somente na janela autorizada). **Rollback:** parar o agente e `sudo systemctl start <serviço do leitor.py>`.
* **Buffer legado somente leitura:** `"camera": {"mode":"external_dir","external_glob":"/srv/.../buffer/*.ts"}` (o agente não grava nem apaga ali).
* **`replay.py` legado como gerador:** `"clip": {"renderer":"external_command","command":["python3","/srv/.../replay.py","--court","{court}","--t0","{t0}","--pre","{pre}","--post","{post}","--out","{out}"]}` (argumentos a confirmar no inventário).

## 6. Inventário do servidor e do código legado (somente leitura, sem executar nada do legado)
**Use primeiro o inventário completo:** [INVENTARIO-SRV01.md](INVENTARIO-SRV01.md) (`scripts/inventario_srv01.py`: serviços, FFmpeg, serial, câmeras sem credenciais, armazenamento, versões, CPU/GPU/rede). O comando abaixo é só a análise estática dos três arquivos.
```bash
python3 /tmp/replay-agora/scripts/inventory_legacy.py /srv/replayagora/botoeira/leitor.py /srv/replayagora/quadra01/app/buffer.py /srv/replayagora/quadra01/app/replay.py --out /tmp/inventario-legado.json
```
O relatório (hash, funções, CLI, chamadas relevantes; credenciais mascaradas) alimenta os adaptadores. **Rollback:** `rm /tmp/inventario-legado.json`.

## 7. Serviço
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now replayagora-agent
systemctl status replayagora-agent --no-pager
journalctl -u replayagora-agent -n 50 --no-pager        # sem senhas/tokens (redação ativa)
sudo -u replayagora PYTHONPATH=/opt/replayagora/agent /opt/replayagora/venv/bin/python -m replay_agent diagnose --config /etc/replayagora/agent.json | head -60
```
**Verificação:** painel da arena → **Diagnóstico**: conexão online, câmeras “transmitindo”, FPS ≈ declarado, buffer crescendo. **Rollback:** `sudo systemctl disable --now replayagora-agent` (o `KillMode=control-group` encerra também os FFmpeg de captura; os arquivos em `/var/lib/replayagora` ficam preservados).

## 8. Rede e segurança
Saída HTTPS (443) para a central e RTSP (554/TCP) para as câmeras; **nenhuma entrada**. Credenciais 0600; rotação automática (`rotateRecommended`) grava o par novo em `credentials.json` antes de usá-lo. `NoNewPrivileges`, `ProtectSystem=strict` e `DeviceAllow` limitam o serviço.

## 9. Remoção completa
```bash
sudo systemctl disable --now replayagora-agent; sudo rm -f /etc/systemd/system/replayagora-agent.service /etc/udev/rules.d/99-replayagora.rules
sudo systemctl daemon-reload && sudo udevadm control --reload
# dados (replays/buffer) só depois de decidir o destino: sudo tar czf /root/replayagora-dados.tgz /var/lib/replayagora && sudo rm -rf /var/lib/replayagora
```
