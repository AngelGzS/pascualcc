"""List all available BingX perpetual futures contracts, grouped by category."""
import requests

BASE = "https://open-api.bingx.com"


def main():
    url = f"{BASE}/openApi/swap/v2/quote/contracts"
    r = requests.get(url, timeout=10)
    data = r.json()

    if not data.get("data"):
        print("Error fetching contracts:", data)
        return

    contracts = data["data"]
    print(f"Total contracts: {len(contracts)}\n")

    # Categorize
    categories = {
        "Indices": [],
        "Stocks": [],
        "Forex": [],
        "Commodities": [],
        "Crypto": [],
    }

    for c in contracts:
        s = c["symbol"]
        if s.startswith("NCSI"):
            categories["Indices"].append(c)
        elif s.startswith("NCSK"):
            categories["Stocks"].append(c)
        elif s.startswith("NCFX"):
            categories["Forex"].append(c)
        elif s.startswith("NCCO"):
            categories["Commodities"].append(c)
        else:
            categories["Crypto"].append(c)

    for cat, items in categories.items():
        if not items:
            continue
        print(f"{'=' * 60}")
        print(f"  {cat} ({len(items)} contracts)")
        print(f"{'=' * 60}")
        for c in sorted(items, key=lambda x: x["symbol"]):
            s = c["symbol"]
            asset = c.get("asset", "")
            currency = c.get("currency", "")
            print(f"  {s:45s} {asset}")
        print()


if __name__ == "__main__":
    main()
