"use client";

import { RiArrowRightLine } from "@remixicon/react";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { AnalysisOrb } from "@/components/ui/effects";
import { Eyebrow } from "@/components/ui/surface";
import { TextField } from "@/components/ui/text-field";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import type { Market } from "@/lib/api/types";
import { nameFromWebsite, parseBusinessInput } from "@/lib/channel-input";

const DEFAULT_MARKET: Market = { country: "IN", language: "en-IN", timezone: "Asia/Kolkata" };

let browserMarket: Market | null = null;

function detectMarket(): Market {
  if (browserMarket) return browserMarket;
  const language = navigator.language || DEFAULT_MARKET.language;
  const country = language.split("-")[1]?.toUpperCase() ?? DEFAULT_MARKET.country;
  browserMarket = { country, language, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
  return browserMarket;
}

const subscribeNever = () => () => {};

const NEXT_STEPS = [
  "We read your website, or route around it if it blocks us, to learn what you sell and how people buy.",
  "We confirm your YouTube channel through real links, never by name alone.",
  "We find 3–10 competitors on YouTube and show you why each one made the list.",
];

export default function NewClientPage() {
  const router = useRouter();
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [name, setName] = useState("");
  const [showSecondary, setShowSecondary] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existing, setExisting] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const market = useSyncExternalStore(subscribeNever, detectMarket, () => DEFAULT_MARKET);

  const parsed = primary.trim() ? parseBusinessInput(primary) : null;
  const secondaryLabel = parsed?.kind === "channel" ? "Your website" : "Your YouTube channel";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setExisting(null);
    const inputs = [primary, secondary].filter((value) => value.trim()).map(parseBusinessInput);
    if (inputs.some((input) => input.kind === "invalid")) {
      setError("Enter a website (example.com) or a YouTube channel (@name or link).");
      return;
    }
    const website = inputs.find((input) => input.kind === "website");
    const channel = inputs.find((input) => input.kind === "channel");
    if (!website && !channel && !name.trim()) {
      setError("Enter a website, a YouTube channel or your business name.");
      return;
    }
    setSubmitting(true);
    try {
      const client = await api.clients.create(
        {
          name: name.trim() || (website?.kind === "website" ? nameFromWebsite(website.url) : "New client"),
          website_url: website?.kind === "website" ? website.url : null,
          channel_ref: channel?.kind === "channel" ? channel.ref : null,
          market,
          market_explicit: false,
          start_onboarding: true,
        },
        idempotencyKey,
      );
      const runQuery = client.onboarding_run ? `?run=${client.onboarding_run.id}` : "";
      router.push(`/c/${client.client.id}/setup/analyze${runQuery}`);
    } catch (caught) {
      if (isApiError(caught) && caught.code === "client_exists") setExisting(caught.message);
      else setError(isApiError(caught) ? caught.message : "Something went wrong. Try again.");
      setSubmitting(false);
    }
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(320px,480px)_1fr] lg:gap-16">
      <form onSubmit={submit} className="flex flex-col gap-5" noValidate>
        <Eyebrow dot>01 · Your business</Eyebrow>
        <h1 className="text-display text-foreground">Make the videos your buyers search for.</h1>
        <p className="text-body-regular text-muted-foreground">Start with whatever you have. One link is enough; we&apos;ll find the rest.</p>

        <TextField
          label="Your website or YouTube channel"
          placeholder="apollohospitals.com or @ApolloHospitalsIndia"
          value={primary}
          onChange={(event) => setPrimary(event.target.value)}
          error={error}
          autoFocus
        />
        {showSecondary ? (
          <TextField label={secondaryLabel} value={secondary} onChange={(event) => setSecondary(event.target.value)} placeholder={parsed?.kind === "channel" ? "example.com" : "@yourchannel"} />
        ) : (
          <button type="button" onClick={() => setShowSecondary(true)} className="w-fit text-body-medium text-info">
            {parsed?.kind === "channel" ? "Add your website too" : "Add your channel too"}
          </button>
        )}
        <TextField
          label="Business name (optional)"
          hint="Enough on its own if you have no website or channel yet."
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Apollo Hospitals"
        />
        <p className="text-caption-1-regular text-muted-foreground">We detect your market from your website&apos;s phone numbers, addresses and prices.</p>
        {existing ? <InfoNote tone="warning">You already added this business. Open it from your clients list.</InfoNote> : null}
        <Button type="submit" variant="primary" size="medium" trailingIcon={RiArrowRightLine} disabled={submitting} className="h-13 rounded-full text-[15px]">
          {submitting ? "Starting…" : "Analyse my business"}
        </Button>
      </form>

      <div className="rounded-hero p-4 shadow-hero sm:p-6" style={{ background: "var(--hero-surface)" }}>
        <div className="flex h-full flex-col gap-6 rounded-panel bg-card/95 p-6 backdrop-blur">
          <div className="flex items-center gap-3">
            <AnalysisOrb state="breathing" label="Waiting to start" />
            <p className="text-body-medium text-foreground">What happens next, live</p>
          </div>
          <ol className="flex flex-col gap-4">
            {NEXT_STEPS.map((step, index) => (
              <li key={step} className="flex gap-3">
                <span className="grid size-6 shrink-0 place-items-center rounded-full bg-secondary text-caption-1-semibold text-foreground">{index + 1}</span>
                <span className="text-body-regular text-foreground">{step}</span>
              </li>
            ))}
          </ol>
          <p className="mt-auto text-caption-1-regular text-subtle-foreground">Everything you&apos;ll see comes from real pages and public YouTube data. Nothing is staged.</p>
        </div>
      </div>
    </div>
  );
}
