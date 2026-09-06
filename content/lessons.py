"""
Learn content.

Short, practical lessons written to be read once and understood, not to be
comprehensive. Each is a few hundred words at most.

House style, which the tests enforce:
  * No hype, no motivational language, no promises about returns.
  * Plain English first, the term second. A reader who does not know what a
    P/E ratio is should not need to already know what a P/E ratio is.
  * Every lesson says what the idea does NOT tell you. That caveat is usually
    the part a beginner most needs and the part most guides leave out.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Lesson", "LESSONS", "LESSONS_BY_KEY", "CATEGORIES", "lesson_for_metric"]


@dataclass(frozen=True)
class Lesson:
    key: str
    title: str
    category: str
    minutes: int
    summary: str          # one line, shown in lists
    body: str             # markdown, a few short paragraphs
    metric: str | None = None   # links a glossary metric to this lesson


CATEGORIES = ["Getting started", "Reading the numbers", "Judging a company", "Investing in the UK"]


LESSONS: list[Lesson] = [
    Lesson(
        key="what_is_a_share", title="What is a share?", category="Getting started", minutes=3,
        summary="What you actually own when you buy one, and where returns come from.",
        body="""
A share is a unit of ownership in a company. Buy one share of a company that has
issued a billion of them and you own a billionth of the business — its factories,
its brands, its cash, and its future profits.

That ownership pays you in two ways. The company may hand out part of its profit
as a **dividend**. And the share itself may become worth more or less, depending
on what other people are willing to pay for it.

The second part is where most of the movement comes from, and it is worth being
clear about what drives it: a share price is simply the price of the last trade.
It reflects what buyers and sellers currently believe about a company, which can
move faster and further than the business underneath it.

**What this does not tell you.** Owning a share gives you no say in day-to-day
decisions and no guarantee of anything. If the company fails, ordinary
shareholders are paid last, after lenders and suppliers — often that means
nothing at all.
""",
    ),
    Lesson(
        key="market_cap", title="Market capitalisation", category="Getting started", minutes=2,
        summary="What a company is worth in the market's eyes, and why the share price alone misleads.",
        body="""
Market capitalisation is the share price multiplied by the number of shares.
It is what the market currently says the whole company is worth.

This matters because **a share price on its own tells you nothing about size**. A
£2 share is not cheaper than a £200 share. A company with two billion £2 shares
is worth far more than one with a million £200 shares. Comparing share prices
between companies is meaningless; comparing market caps is not.

Size is also a rough guide to how a company tends to behave. Very large companies
usually grow more slowly and move less sharply. Smaller ones can grow faster and
fall harder, and are often followed by fewer analysts.

**What this does not tell you.** Market cap is what the company is *priced* at,
not what it is *worth*. Those are different claims, and the gap between them is
the whole of investing.
""",
    ),
    Lesson(
        key="revenue_vs_profit", title="Revenue versus profit", category="Reading the numbers", minutes=3,
        summary="Why a company with rising sales can still be losing money.",
        body="""
**Revenue** is everything a company sold. **Profit** is what is left after paying
for it all — materials, wages, rent, interest, tax.

The two can move in opposite directions, and often do. A company can grow revenue
quickly by cutting prices or spending heavily to win customers, and end up making
less money than before. Growing sales is not the same as building a better
business.

Profit is also more easily shaped by accounting choices than revenue is. That is
why cash flow is often a better guide to health than reported profit — cash is
harder to present flatteringly.

**What to watch.** Rising revenue with falling margins usually means growth is
being bought rather than earned.
""",
    ),
    Lesson(
        key="profit_margins", title="Profit margins", category="Reading the numbers", minutes=3,
        summary="How much of each pound of sales the company actually keeps.",
        body="""
A margin is profit expressed as a percentage of revenue. A 20% net margin means
the company keeps 20p of every £1 it sells.

Higher margins give a company room to absorb rising costs, price competition or a
bad year without falling into losses. They often indicate something durable —
a strong brand, a cost advantage, or a product customers will not easily swap.

**Operating margin** covers the core business before interest and tax, so it
shows whether the actual operation is profitable, separately from how it is
financed. **Net margin** is what survives everything.

**What this does not tell you.** Margins vary enormously by industry. A
supermarket running on 3% is not unhealthy and a software company on 25% is not
necessarily excellent. Compare like with like.
""",
        metric="profitMargins",
    ),
    Lesson(
        key="pe_ratio", title="P/E ratio: when is a share actually expensive?",
        category="Reading the numbers", minutes=4,
        summary="The most quoted valuation measure, and the most misused.",
        body="""
The price-to-earnings ratio divides the share price by the profit earned per
share over the last year. A P/E of 20 means you are paying £20 for every £1 of
annual profit.

Read plainly, it is a rough payback period: at 20, the company would need twenty
years at current profits to earn back what you paid. That framing makes the
trade-off obvious — a higher number means you are paying more for the same
earnings today.

A high P/E is not automatically bad. Investors pay more when they expect profits
to grow, and sometimes they are right. But it does mean more of the outcome
depends on that growth actually arriving. A company on 40 has less room to
disappoint than one on 12.

**Forward P/E** uses analysts' forecast profits instead of last year's. If it is
lower than the current P/E, analysts expect earnings to rise. Those are
estimates, and they are revised often.

**What this does not tell you.** P/E is meaningless for a company making no
profit, and it varies hugely between industries. It also says nothing about debt
— two companies on the same P/E can carry very different risk.
""",
        metric="trailingPE",
    ),
    Lesson(
        key="price_to_sales", title="Price to sales", category="Reading the numbers", minutes=2,
        summary="The fallback measure when a company has no profits yet.",
        body="""
Price to sales divides the company's market value by its annual revenue. It
exists mainly because P/E cannot be calculated when there are no profits.

It is most useful for younger, fast-growing companies that are deliberately
spending ahead of earnings. It is least useful as a cross-industry comparison:
normal levels differ by an order of magnitude between, say, a supermarket and a
software business.

**What this does not tell you.** Revenue is not profit. A low price-to-sales
figure on a business that never converts sales into cash is not a bargain.
""",
        metric="priceToSalesTrailing12Months",
    ),
    Lesson(
        key="free_cash_flow", title="Free cash flow", category="Reading the numbers", minutes=3,
        summary="The money actually left over, and why it beats reported profit.",
        body="""
Free cash flow is the cash a company has left after running the business and
paying for the equipment and property it needs to keep going.

It matters because it is what actually funds dividends, debt repayment,
buybacks and reinvestment. Reported profit is an accounting figure shaped by
judgements about depreciation and timing. Cash is harder to present favourably.

A company can report healthy profits while burning cash, and that gap is one of
the more reliable early warnings there is.

**What this does not tell you.** A single year can be distorted by one large
investment. A company building a factory may show poor free cash flow while doing
exactly the right thing.
""",
        metric="freeCashflow",
    ),
    Lesson(
        key="debt", title="Debt", category="Judging a company", minutes=3,
        summary="Why borrowing magnifies both good years and bad ones.",
        body="""
Debt is not inherently bad. Borrowing to build something that earns more than the
interest makes shareholders better off.

The problem is that debt is unforgiving. Interest must be paid whatever happens
to profits, so borrowing magnifies results in both directions. A modest downturn
at a heavily indebted company can become a crisis; the same downturn at a
debt-free one is an inconvenience.

**Debt to equity** compares what a company has borrowed with the shareholders'
money in the business. What counts as normal varies enormously — banks and
utilities operate on leverage by design, software companies rarely need it.

**What this does not tell you.** The ratio ignores whether the debt is cheap,
when it is due, and whether profits comfortably cover the interest. A company
with high but long-dated, low-rate debt may be safer than the number suggests.
""",
        metric="debtToEquity",
    ),
    Lesson(
        key="roe", title="Return on equity", category="Judging a company", minutes=3,
        summary="How efficiently a company turns shareholders' money into profit.",
        body="""
Return on equity is profit divided by the shareholders' money invested in the
business. A consistent 20% suggests the company turns each pound of capital into
twenty pence of annual profit — a sign it has something worth reinvesting in.

Sustained high returns are one of the more meaningful signs of quality, because
competition usually erodes them. A company holding them for years typically has
some advantage protecting it.

**What this does not tell you.** Borrowing inflates the figure. Equity is what is
left after debts, so loading up on debt shrinks the denominator and flatters the
return. Always read it alongside debt — which is why this platform scores the two
in the same category.
""",
        metric="returnOnEquity",
    ),
    Lesson(
        key="dividends", title="Dividends", category="Judging a company", minutes=3,
        summary="Cash paid to shareholders, and why a big yield can be a warning.",
        body="""
A dividend is a share of profit paid out in cash. The **yield** is that payment as
a percentage of the share price — £2 a year on a £50 share is a 4% yield.

Dividends appeal because they are tangible and reasonably predictable. Companies
are reluctant to cut them, so a long unbroken record says something about
stability.

But the yield moves with the price, and that is where people are caught out. A
yield rises when the price falls. An unusually high yield often means the market
expects the dividend to be cut, not that you have found free income.

**What this does not tell you.** A dividend is not a return if the shares fall by
more. Money paid out is also money not reinvested — for a fast-growing company,
paying dividends can be the worse choice.
""",
    ),
    Lesson(
        key="growth_vs_value", title="Growth and value", category="Judging a company", minutes=3,
        summary="Two ways of choosing companies, and why the labels mislead.",
        body="""
**Growth** investing buys companies expected to expand quickly, accepting a high
price for future earnings. **Value** investing buys companies priced low relative
to what they already earn, on the view that the market is being too pessimistic.

Both work and both fail. Growth suffers when expectations are not met, because a
high price leaves no room for disappointment. Value suffers when a company is
cheap for a good reason and simply keeps deteriorating — a *value trap*.

The distinction is less useful than it sounds. What actually matters is whether
you are paying a sensible price for what a business realistically achieves, and
that question applies to both.

**What this does not tell you.** Neither label says anything about quality. There
are excellent and terrible companies in both.
""",
    ),
    Lesson(
        key="momentum", title="Momentum", category="Judging a company", minutes=3,
        summary="What a rising price does and does not tell you.",
        body="""
Momentum describes how a share price has moved recently. Prices that have risen
have historically shown some tendency to keep rising for a while — and also to
reverse, sometimes sharply.

The important point is what momentum measures: **the price, not the company**. A
business with collapsing revenue can have excellent momentum. A strong company can
sit flat for years.

That is why this platform scores momentum separately from business quality and
never blends them. Seeing both lets you tell the difference between a good company
whose price has risen and a poor company whose price has risen — which look
identical if you only track one number.

**What this does not tell you.** Momentum is a description of the past. It is not
a forecast, and a high momentum score is not a reason to buy anything.
""",
    ),
    Lesson(
        key="diversification", title="Diversification", category="Judging a company", minutes=3,
        summary="What spreading your money does, and what it cannot protect against.",
        body="""
Diversification means holding enough different investments that no single one can
do serious damage. If one holding is a tenth of your portfolio, losing all of it
costs you a tenth. If it is everything, it costs you everything.

The subtlety is that diversification depends on holdings behaving *differently*,
not just on counting them. Ten technology companies are far less diversified than
they look — they tend to fall together, for the same reasons, at the same time.

**What this does not tell you.** Diversification does not protect against a fall
in the whole market. When everything drops together, spreading your money across
more of it does not help.
""",
    ),
    Lesson(
        key="risk", title="Risk", category="Judging a company", minutes=3,
        summary="What volatility measures, and the risk it misses entirely.",
        body="""
In investing, risk is usually measured as volatility — how much a price swings
about. **Beta** compares that swing with the wider market: a beta of 1.5 means the
shares have historically moved about 1.5% for every 1% market move, in both
directions.

That is a useful description of the ride. It is a poor description of danger. The
risk most people actually care about is permanent loss: the company deteriorates,
or you are forced to sell at a bad moment.

A stable share price can hide a slowly failing business. A volatile one can belong
to a sound company in a jumpy sector.

**What this does not tell you.** Volatility is backward-looking, and higher
volatility does not mean higher expected returns. It means larger swings.
""",
        metric="beta",
    ),
    Lesson(
        key="isa", title="Stocks and Shares ISA", category="Investing in the UK", minutes=4,
        summary="How the UK's tax wrapper works, and what it does not do.",
        body="""
A Stocks and Shares ISA is a **wrapper**, not an investment. You choose what goes
inside it; the wrapper decides how it is taxed.

Inside one, gains and dividends are free of UK capital gains tax and dividend tax,
and there is nothing to declare. Outside one, both can be taxable once you pass
the annual allowances.

There is a yearly limit on how much you can pay in, set by the government and
occasionally changed. Unused allowance does not roll over.

**What this does not tell you.** An ISA offers no protection from losses — the
investments inside can fall like any others. The tax advantage only matters if
there are gains to shelter, and it does not make an unsuitable investment
suitable.

Tax rules change and depend on your circumstances. This is general information,
not tax advice.
""",
    ),
    Lesson(
        key="uk_vs_us", title="UK and US shares", category="Investing in the UK", minutes=3,
        summary="Currency, fees and the practical differences for a UK investor.",
        body="""
UK-listed shares usually carry a `.L` suffix and trade in pounds, often quoted in
pence rather than pounds — a price of 250 frequently means £2.50, which catches
people out.

US shares trade in dollars, so a UK investor takes on **currency risk**: if the
pound strengthens against the dollar, your US holdings are worth less in pounds
even if the share price has not moved. That works both ways.

Brokers typically charge an FX fee to convert, and it is often a larger cost than
the trading commission. It is worth knowing what yours charges before assuming a
small position is cheap to hold.

**Stamp duty** of 0.5% applies to most UK share purchases and not to US ones.

**What this does not tell you.** None of this says which market is a better place
to invest. It is a description of the friction, not the opportunity.
""",
    ),
    Lesson(
        key="how_to_research", title="How to research a company", category="Getting started", minutes=4,
        summary="A practical order to work through, and when to stop.",
        body="""
A workable sequence, roughly in order of what rules a company out fastest:

**Understand what it sells.** If you cannot explain how the company makes money
in a sentence, nothing else you read will mean much.

**Check it is profitable and solvent.** Margins, cash flow and debt. This
eliminates a lot quickly.

**Look at the direction of travel.** Are revenue and profit growing, flat, or
shrinking? One year is noise; three is a trend.

**Only then look at the price.** Valuation is the last question, not the first.
A wonderful company at an impossible price is still a bad purchase, and a cheap
price on a failing business is not a bargain.

**Write down what would change your mind.** If you cannot say what would make you
wrong, you are not researching — you are collecting reasons for a decision you
have already made.

**What this does not tell you.** No amount of research removes uncertainty. The
aim is to understand what you are buying and what could go wrong, not to be
certain.
""",
    ),
]

LESSONS_BY_KEY = {lesson.key: lesson for lesson in LESSONS}
_BY_METRIC = {lesson.metric: lesson for lesson in LESSONS if lesson.metric}


def lesson_for_metric(field: str) -> Lesson | None:
    """The lesson that explains a scored metric, if one exists."""
    return _BY_METRIC.get(field)
