import { toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";

const OAUTH_REASONS: Record<string, string> = {
  access_denied: "Google sign-in was cancelled.",
  channel_mismatch: "That Google account owns a different channel than the one this client tracks.",
  no_channel: "That Google account has no YouTube channel.",
  invalid_state: "The sign-in link expired. Try again.",
  failed: "Google didn't complete the sign-in. Try again.",
};

export function oauthReason(reason: string | null): string {
  return OAUTH_REASONS[reason ?? ""] ?? OAUTH_REASONS.failed;
}

export async function startYouTubeConnect(clientId: string): Promise<void> {
  try {
    const { authorization_url } = await api.integrations.youtubeConnect(clientId);
    if (authorization_url.startsWith("http")) window.location.assign(authorization_url);
    else toast("Connecting isn't available with demo data", { description: "With a real workspace this opens Google's consent screen." });
  } catch (caught) {
    if (isApiError(caught) && caught.code === "integration_not_configured") {
      toast.error("YouTube sign-in isn't set up on this server", { description: caught.message });
      return;
    }
    toast.error(isApiError(caught) ? caught.message : "Couldn't start connecting. Try again.");
  }
}
