import { redirect } from "next/navigation";

// the trend-led ideas page replaced the old daily brief as the landing page
export default async function TodayPage({ params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  redirect(`/c/${clientId}/ideas`);
}
