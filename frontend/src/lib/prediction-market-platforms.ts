export interface PredictionMarketPlatform {
  key: string;
  name: string;
  chain: string;
  colorClass: string;
  homepageUrl: string;
  searchUrl?: (question: string) => string;
  activeDiscovery: boolean;
}

export const PREDICTION_MARKET_PLATFORMS: PredictionMarketPlatform[] = [
  {
    key: "polymarket",
    name: "Polymarket",
    chain: "Polygon",
    colorClass: "bg-[#555EEF]",
    homepageUrl: "https://polymarket.com/markets",
    searchUrl: (question) => `https://polymarket.com/markets?_q=${encodeURIComponent(question)}`,
    activeDiscovery: true,
  },
  {
    key: "kalshi",
    name: "Kalshi",
    chain: "Off-chain",
    colorClass: "bg-[#1ABAFF]",
    homepageUrl: "https://kalshi.com/markets",
    searchUrl: (question) => `https://kalshi.com/markets?search=${encodeURIComponent(question)}`,
    activeDiscovery: true,
  },
  {
    key: "opinion",
    name: "Opinion",
    chain: "BNB Chain",
    colorClass: "bg-[#F0B90B]",
    homepageUrl: "https://app.opinion.trade/trending",
    activeDiscovery: false,
  },
  {
    key: "limitless",
    name: "Limitless",
    chain: "Base",
    colorClass: "bg-[#0052FF]",
    homepageUrl: "https://limitless.exchange/",
    activeDiscovery: false,
  },
  {
    key: "predict_fun",
    name: "Predict.fun",
    chain: "BNB Chain",
    colorClass: "bg-[#7C3AED]",
    homepageUrl: "https://predict.fun/",
    activeDiscovery: false,
  },
  {
    key: "probable",
    name: "Probable",
    chain: "BNB Chain",
    colorClass: "bg-[#10B981]",
    homepageUrl: "https://probable.finance/",
    activeDiscovery: false,
  },
];

export function marketPlatformUrl(
  platform: PredictionMarketPlatform,
  question: string,
) {
  return platform.searchUrl ? platform.searchUrl(question) : platform.homepageUrl;
}
