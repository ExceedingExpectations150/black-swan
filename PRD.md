# ChaosNet: Neuro-Symbolic Market Twin (Master PRD)

## 1. System Overview
ChaosNet is an enterprise multi-agent market simulation platform built to stress-test financial and logistics ecosystems during "Black Swan" macro shocks. It pits natural-language behavioral AI (Gemma cohorts) against quantitative time-series AI (TimesFM) inside a Continuous Double Auction (CDA) stock market.

## 2. Technical Stack & Constraints
- **Backend:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, Pydantic, WebSockets, aiohttp.
- **Database:** SQLite (local dev fallback) / PostgreSQL (production target).
- **AI Computing (Strict Requirement - NO MOCKS):**
  - Behavioral Cohorts: Google Generative Language API (`gemma-2-27b-it` / `gemini-1.5-flash`) via `aiohttp`.
  - Quantitative Algorithms: Google `timesfm` package running locally via PyTorch (`cuda`/`cpu`).
- **Frontend:** Next.js 14+ (App Router), TypeScript, Tailwind CSS, Recharts, Lucide React icons.

## 3. Database Schema Definitions
### Table: `world_states`
- `tick_id` (Integer, Primary Key)
- `current_price` (Float, authoritative clearing valuation)
- `news_headline` (String, current active macro shock text)
- `system_stress_index` (Float, 0.0 to 1.0 calculated via price volatility)
- `timestamp` (DateTime)

### Table: `agent_states`
- `agent_id` (String UUID, Primary Key)
- `agent_type` (Enum String: "gemma_retail_cohort" | "timesfm_institutional")
- `cash_balance` (Float, starting baseline ~100,000.0)
- `stock_inventory` (Integer, starting baseline ~1,000)
- `risk_tolerance` (Float, 0.1 to 0.9)
- `is_bankrupt` (Boolean, triggered when cash <= 0)

### Table: `order_books`
- `order_id` (String UUID, Primary Key)
- `tick_id` (Integer, Foreign Key to world_states)
- `agent_id` (String UUID, Foreign Key to agent_states)
- `order_type` (Enum String: "BUY" | "SELL")
- `quantity` (Integer)
- `limit_price` (Float)
- `status` (Enum String: "PENDING" | "FILLED" | "CANCELLED")

## 4. Architectural Rules for AI Agents
1. **Never write hardcoded stubs or random number generators for AI outputs.**
2. **Handle API Rate Limits Dynamically:** Ensure the Gemini API client monitors HTTP status 429 and rotates between primary/backup API keys and alternative model endpoints automatically.
3. **TimesFM Tensor Input:** Feed the last 512 market clearing prices into `TimesFm.forecast()`. Ensure arrays shorter than 32 points are automatically left-padded with the initial price.