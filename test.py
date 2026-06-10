import requests
import pandas as pd

# 1. Λήψη 24h ticker για όλα τα σύμβολα
url = "https://api.binance.com/api/v3/ticker/24hr"
response = requests.get(url)
data = response.json()

# 2. Δημιουργία DataFrame
df = pd.DataFrame(data)

# 3. Επιλογή μόνο συμβόλων USDT (προαιρετικά)
df_usdt = df[df['symbol'].str.endswith('USDT')].copy()

# 4. Μετατροπή όγκου σε αριθμό (quoteVolume = όγκος σε USDT)
df_usdt['quoteVolume'] = df_usdt['quoteVolume'].astype(float)

# 5. Ταξινόμηση και επιλογή Ν μεγαλύτερων
N = 10
top_N = df_usdt.nlargest(N, 'quoteVolume')[['symbol', 'quoteVolume']]

print(top_N)