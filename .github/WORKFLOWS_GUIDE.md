# GitHub Actions — návod

Tenhle repozitář má pár automatizací (GitHub Actions), které ti usnadní práci a propojí
commity/větve/PR s issues. **Fungují hned po mergnutí tohoto PR — nic nemusíš nastavovat.**

## Co se děje automaticky

| Workflow | Kdy se spustí | Co udělá |
|---|---|---|
| Issue Prefixer | založíš issue | přejmenuje titulek na `PPDB-<číslo>: …` |
| Branch Issue Linker | pushneš větev `feature/…`, `bugfix/…`, `docs/…` | napíše komentář do issue, že se na něm dělá |
| PR Open Notification | otevřeš PR | komentář do issue s odkazem na PR |
| PR Merged Notification | mergneš PR | komentář do issue, že je hotovo |
| CI: Tests | **každý push i PR** | spustí `pytest` a ukáže ✅/❌ |

## Co potřebuješ dělat ty

1. **Testy:** dej soubory `test_*.py` do složky `tests/`. Při každém pushi se samy spustí.
   Dokud žádné testy nemáš, CI stejně projde (nezablokuje tě).
   ```python
   # tests/test_example.py
   def test_basic():
       assert 1 + 1 == 2
   ```

2. **Aby fungovalo propojení s issues** (volitelné, ale doporučené):
   - Větve pojmenuj `feature/PPDB-12_neco` (číslo = číslo issue).
   - Do titulku PR dej `PPDB-12` (např. `PPDB-12: přidat scraper`).
   - Když to neuděláš, nic se nerozbije — jen se nepošle komentář do issue.

## Volitelná nastavení (nemusíš hned)

- **Změnit prefix `PPDB`:** Settings → Secrets and variables → **Actions** → Variables →
  přidej `PROJECT_PREFIX` s jinou hodnotou. (Bez toho se použije výchozí `PPDB`.)
- **Kdyby se komentáře do issues nepsaly:** Settings → Actions → General → *Workflow permissions*
  → přepni na **Read and write permissions** → Save.
- **Testy jako povinná brána** před merge do `main`: Settings → Branches → přidej pravidlo na
  `main` → *Require status checks* → vyber `tests`.

## Kdyby něco

Automatizace jsou napsané tak, aby **nikdy neblokovaly** tvůj push (kromě padlých testů, pokud si je
nastavíš jako povinné). Když nějaká akce „nic neudělá", většinou jen nesedělo číslo issue v názvu —
to je v pořádku. S nastavením ti pomůže Dominika.
