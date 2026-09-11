import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sweetwater · ops window",
  description: "What the agents decided. Reads sw_ops through the M8 read API and nothing else.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
