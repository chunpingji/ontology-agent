import { redirect } from "next/navigation";

// 兼容旧地址；创建统一在列表向导中完成，编辑页只接收已保存的模板 ID。
export default function CreateTemplatePage() {
  redirect("/settings/ast-templates");
}
