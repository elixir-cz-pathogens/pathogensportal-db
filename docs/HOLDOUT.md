# Holdout — nedotknutelné validační epizody

Vyhlášeno 2026-09-06 (audit fáze 0, issue #56). Smysl: systém laděný, „dokud
nechytne známou epidemii“, je optimalizovaný na jeden bod a jeho výkonnostní
čísla jsou bezcenná. Proto se část dokumentovaných epizod zmrazuje **předem**
a smí se použít až při závěrečném vyhodnocení (brány C a D), nikdy při vývoji.

## Vývojová epizoda (přiznaně kontaminovaná)

- **Pertuse 2024** (první signál 11/2023) — používala se při ladění podlah
  prahu a při návrhu pravidel; její zachycení je nutná podmínka, ale **není**
  důkaz kvality.

## Holdout (na tyhle se při vývoji NESAHÁ)

| Epizoda | Období signálů (backtest v0.3.0) | Poznámka |
|---|---|---|
| Akutní hepatitida A | 2021-07 … 2025-12 (23 měs., max 599/měs. ČR) | běžící epidemie |
| Spála [scarlatina] | 2022-10 … 2023-08 (11 měs., max 973) | post-covidový rebound |
| Příušnice [parotitis] | 2022-05 … 2024-08 (16 měs., max 136) | vícevlnná epizoda |

Pravidla:

1. Žádné rozhodnutí o parametrech, prazích, vahách ani architektuře se
   nezdůvodňuje chováním na holdout epizodách.
2. Backtestové výpisy nad holdoutem se během vývoje negenerují a necitují
   (agregátní čísla přes všechny řady jsou v pořádku — jednotlivé epizody ne).
3. Holdout se otevře jednorázově při vyhodnocení bran C/D; výsledek se
   zaznamená, ať dopadne jakkoli.
4. Změna tohoto seznamu vyžaduje zdůvodnění v PR a znamená, že dosavadní
   vývoj mohl být kontaminován — uvádí se to pak v každém validačním reportu.
