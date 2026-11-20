"use client";

import dynamic from "next/dynamic";

// the dismiss and done popovers need react-aria; cards render after a client fetch, so this chunk is usually in by then
export const LazyFeedbackActions = dynamic(() => import("./feedback-actions").then((module) => module.FeedbackActions), {
  ssr: false,
  loading: () => <div aria-hidden className="h-9" />,
});
