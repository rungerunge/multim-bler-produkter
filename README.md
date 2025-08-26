# Multimøbler Produkter - Venture Design Updater

Automatisk opdatering af Venture Design produkter i Shopify.

## Funktioner

- **Billede-swap**: Bytter position 1 og 2 på produktbilleder
- **Pris-opdatering**: Sætter pris til cost × 1.75 (25% moms + 40% markup)
- **Resume capability**: Husker fremskridt og kan genstartes
- **Robust error handling**: Fortsætter selvom enkelte produkter fejler

## Scripts

### `robust_venture_fix.py`
Hovedscript til opdatering af alle Venture Design produkter.

**Usage:**
```bash
# Dry-run (test)
python robust_venture_fix.py --dry-run

# Apply changes
python robust_venture_fix.py --apply

# Apply with limit
python robust_venture_fix.py --apply --limit 100

# Reset progress and start over
python robust_venture_fix.py --apply --reset
```

### Environment Variables
```bash
export SHOPIFY_DOMAIN=multimobler.myshopify.com
export SHOPIFY_TOKEN=your_shopify_token
```

## Railway Deployment

For at køre på Railway over natten:

1. Deploy til Railway
2. Sæt environment variables
3. Start med `python robust_venture_fix.py --apply`

## Progress Tracking

Fremskridt gemmes i `logs/venture_progress.json` og kan resumeres hvis scriptet afbrydes.

## Files

- `robust_venture_fix.py` - Hovedscript
- `requirements.txt` - Python dependencies
- `logs/` - Progress og log filer
- `backups/` - Backup filer

## Status

Scriptet processerer ~5554 Venture Design produkter med hastighed på ~2 sekunder per produkt.
