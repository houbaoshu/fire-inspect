import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <main className="standalone-state">
      <div className="state-mark" aria-hidden="true">404</div>
      <h1>页面不存在</h1>
      <p>页面地址可能已变化，或你没有访问该对象的权限。</p>
      <Link className="button button-primary" to="/inspections">返回检查列表</Link>
    </main>
  );
}
