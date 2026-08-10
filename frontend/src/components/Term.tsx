import { useState } from 'react'

/**
 * Hover-to-define finance jargon.
 *
 * The product's stated audience may know nothing about investing, so any term
 * that would send them to a search engine gets a plain-English definition in
 * place. The definitions deliberately explain *why the number matters* rather
 * than restating the formula — "P/E is price divided by earnings" helps nobody.
 */
const GLOSSARY: Record<string, string> = {
  'p/e': "How many years of today's profits you're paying for one share. A high number means investors expect profits to grow a lot; if they don't, the price usually falls.",
  'forward p/e':
    "Same idea as P/E, but using profits analysts *expect* next year rather than last year's. Lower than the normal P/E means profits are forecast to grow.",
  valuation:
    'How expensive the shares are relative to what the company actually earns or owns — not the share price itself. A £500 share can be cheap and a £5 share expensive.',
  margin:
    'How much of each pound of sales the company keeps as profit. Higher margins mean more pricing power and more cushion when costs rise.',
  'gross margin':
    'What is left from sales after the direct cost of making the product — before wages, marketing and everything else.',
  'operating margin':
    'Profit from the core business as a share of sales, after normal running costs. A good measure of whether the business itself works.',
  'free cash flow':
    'Actual cash left over after running the business and paying for equipment. Harder to massage than profit, so investors trust it more.',
  volatility:
    'How much the price swings around. High volatility means big moves in both directions — it is a measure of turbulence, not of direction.',
  drawdown:
    'The worst fall from a peak to a low. If a stock has a 40% drawdown history, it has previously lost 40% of its value at some point.',
  'moving average':
    "The average price over recent months, used to smooth out daily noise. A price above its averages is usually described as an uptrend.",
  'moving averages':
    "The average price over recent months, used to smooth out daily noise. A price above its averages is usually described as an uptrend.",
  moat: 'Whatever stops competitors from stealing the business — a brand, a network, patents, or switching costs. It is what keeps profits high for years rather than months.',
  momentum:
    'The tendency of a price that has been rising to keep rising for a while (and the same downward). Matters over weeks; means little over years.',
  'debt-to-equity':
    'How much the company has borrowed compared with what the owners have put in. High borrowing magnifies both good and bad years.',
  'current ratio':
    'Whether the company has enough short-term assets to cover its short-term bills. Below 1 means it is relying on cash still coming in.',
  eps: 'Earnings per share — the profit attributable to each individual share. The number most valuation measures are built on.',
  'dividend yield':
    "The cash paid out to shareholders each year as a percentage of the share price. A 3% yield means £3 a year on every £100 invested.",
  'price target':
    'An analyst’s guess at where the price will be in a year. Treat it as an opinion with a number attached, not a forecast that has been tested.',
  '10-k': 'A company’s annual report to the US regulator. Legally binding, which makes it far more reliable than a press release.',
  '10-q': 'A company’s quarterly report to the US regulator — a shorter, more frequent version of the annual report.',
  '8-k': 'A filing companies must make when something material happens between scheduled reports — an acquisition, a resignation, a big contract.',
  '20-f': 'The annual report filed by non-US companies listed in America. The foreign equivalent of a 10-K.',
  'risk factors':
    'The section of the annual report where the company itself must list what could go wrong. Written by lawyers, but the most honest list you will find.',
  peg: 'The P/E divided by the growth rate — an attempt to judge whether a high price is justified by fast growth. Below 1 is traditionally considered cheap.',
  beta: 'How much the stock moves relative to the whole market. Below 1 means it swings less than the market; above 1, more.',
}

export function Term({ children }: { children: string }) {
  const [open, setOpen] = useState(false)
  const definition = GLOSSARY[children.toLowerCase().trim()]
  if (!definition) return <>{children}</>

  return (
    <span
      className="relative cursor-help underline decoration-dotted decoration-faint underline-offset-4"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      tabIndex={0}
    >
      {children}
      {open && (
        <span
          role="tooltip"
          className="absolute bottom-full left-1/2 z-50 mb-2 w-72 -translate-x-1/2 rounded-lg border border-line bg-surface-2 p-3 text-xs leading-relaxed font-normal text-text shadow-2xl"
        >
          <span className="mb-1 block font-semibold text-accent">{children}</span>
          {definition}
        </span>
      )}
    </span>
  )
}

/** Wraps any glossary terms found in a string, leaving the rest untouched. */
export function AutoTerms({ text }: { text: string }) {
  const keys = Object.keys(GLOSSARY).sort((a, b) => b.length - a.length)
  const pattern = new RegExp(
    `\\b(${keys.map((k) => k.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')).join('|')})\\b`,
    'gi',
  )

  const parts: (string | { term: string })[] = []
  let last = 0
  let seen = new Set<string>()
  for (const match of text.matchAll(pattern)) {
    const key = match[0].toLowerCase()
    // Define a term the first time it appears; after that it's just noise.
    if (seen.has(key)) continue
    seen.add(key)
    if (match.index! > last) parts.push(text.slice(last, match.index))
    parts.push({ term: match[0] })
    last = match.index! + match[0].length
  }
  parts.push(text.slice(last))

  return (
    <>
      {parts.map((part, i) =>
        typeof part === 'string' ? (
          <span key={i}>{part}</span>
        ) : (
          <Term key={i}>{part.term}</Term>
        ),
      )}
    </>
  )
}
