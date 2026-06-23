import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) for slim containers.
  output: "standalone",
  // 旧链接兜底（FR-011 / contracts/routes.md）：导航重构后旧路径 308 到新位置。
  async redirects() {
    return [
      { source: "/extraction", destination: "/entities/extraction", permanent: true },
      { source: "/reasoning", destination: "/analysis", permanent: true },
      { source: "/knowledge-graph", destination: "/analysis", permanent: true },
      { source: "/", destination: "/overview", permanent: true },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
