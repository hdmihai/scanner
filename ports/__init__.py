"""
ports - porturile nucleului: contractele prin care core/ vorbeste cu lumea exterioara.

Un port descrie CE are nevoie nucleul, nu DE UNDE vine: date de piata
(market_data), documente salvate (store), notificari (notifier). Adaptoarele din
adapters/ implementeaza porturile - ccxt pentru burse, fisiere JSON pentru
stocare, Telegram pentru notificari - iar radacina compozitiei
(crypto_ai_scanner.py) le leaga de nucleu. Nucleul si adaptoarele depind de
porturi; porturile nu depind de nimic.
"""
