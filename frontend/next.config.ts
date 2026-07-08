import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle (.next/standalone) for slim containers.
  output: "standalone",
  // dev 模式下经公网域名（隧道+nginx 反代）访问时，放行该来源，
  // 否则 Next 会拒绝跨域 dev 请求，导致 HMR (/_next/webpack-hmr) 握手失败。
  allowedDevOrigins: ["sldpr-demo.infilake.com"],
  // 旧链接兜底（FR-011 / contracts/routes.md）：导航重构后旧路径 308 到新位置。
  async redirects() {
    return [
      { source: "/extraction", destination: "/entities/extraction", permanent: true },
      { source: "/reasoning", destination: "/analysis", permanent: true },
      { source: "/knowledge-graph", destination: "/analysis", permanent: true },
      { source: "/", destination: "/overview", permanent: true },
      // 015 supersession (FR-005 / clarify Q3): legacy routes carry their
      // functionality onto the new pages and 308-redirect there.
      { source: "/integration", destination: "/connector", permanent: true },
      { source: "/approvals", destination: "/approval", permanent: true },
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
