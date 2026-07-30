import os, sys
sys.stdout.reconfigure(encoding='utf-8')

base_5m = r'C:\Users\Dave\Documents\bot-bybit-5m'
base_bb = r'C:\Users\Dave\Documents\bot-bybit'

files = ['bot.py', 'exchange.py', 'notifier.py', 'trade_journal.py', 'risk_manager.py', 'config.py']

print("=" * 65)
print("  COMPARACION bot-bybit vs bot-bybit-5m")
print("=" * 65)

for fn in files:
    path_5m = os.path.join(base_5m, fn)
    path_bb = os.path.join(base_bb, fn)
    exist_5m = os.path.exists(path_5m)
    exist_bb = os.path.exists(path_bb)
    if not exist_5m or not exist_bb:
        print(f"{fn}: FALTA en alguno de los repos")
        continue
    txt_5m = open(path_5m, encoding='utf-8', errors='replace').read()
    txt_bb = open(path_bb, encoding='utf-8', errors='replace').read()
    same = (txt_5m == txt_bb)
    tag = "IDENTICO" if same else "DIFERENTE"
    print(f"  {fn:25s}: {tag} | 5m={len(txt_5m):6d}b  bb={len(txt_bb):6d}b")

print()
# Verificar si los metodos nuevos existen en cada archivo del bot-bybit
checks = {
    'exchange.py':      ['get_last_closed_pnl'],
    'trade_journal.py': ['close_trade', 'get_open_trades'],
    'bot.py':           ['_reconcile_position', '_pending_trade'],
    'notifier.py':      ['TELEGRAM_PROXY', 'self._proxies'],
}
print("=" * 65)
print("  METODOS NUEVOS presentes en bot-bybit")
print("=" * 65)
for fn, methods in checks.items():
    path = os.path.join(base_bb, fn)
    txt  = open(path, encoding='utf-8', errors='replace').read()
    for m in methods:
        present = m in txt
        status = "PRESENTE" if present else "FALTA   <-- NECESITA CORRECCION"
        print(f"  {fn:25s} | {m:30s}: {status}")
