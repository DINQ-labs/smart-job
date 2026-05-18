import { redirect } from "next/navigation";
import { defaultLocale } from "@/lib/i18n";

// 强制动态渲染:否则根路由的 redirect() 会被静态预渲染成「307 但无 Location 头」,
// 浏览器只能靠 JS 兜底跳转(会闪一下)。force-dynamic 让它每次请求服务端渲染,
// 发出标准的 307 + Location,浏览器/爬虫都能直接跳。
export const dynamic = "force-dynamic";

export default function RootPage() {
  redirect(`/${defaultLocale}`);
}
