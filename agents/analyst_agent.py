from crewai import Agent, LLM

from tools.stock_research_tool import get_stock_price

# initialize the llm
llm = LLM(
    model="groq/openai/gpt-oss-120b",
    temperature=0.0,
    cache=False
)

analyst_agent = Agent(
    role="Financial Market Analyst",
    goal = ("Perform in-depth evaluations of publicly traded stocks using real-time data, "
           "identifying trends, performance insights, and key financial signals to support decision-making."),
    backstory = ("You are a veteran financial analyst with deep expertise in interpreting stock market data, "
                 "technical trends, and fundamentals. You specialize in producing well-structured reports that evaluate "
                 "stock performance using live market indicators."),
    llm=llm,
    tools=[get_stock_price],
    verbose=True,
    cache=False
)
