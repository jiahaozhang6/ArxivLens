import { GitFork, Rss } from 'lucide-react'

const PROJECT_URL = 'https://github.com/jiahaozhang6/ArxivLens'

export function ProjectFooter() {
  return (
    <footer className="project-footer">
      <div className="project-footer-item">
        <span>开源项目</span>
        <a href={PROJECT_URL} target="_blank" rel="noreferrer" title="查看 ArxivLens 开源项目">
          <GitFork size={15} />
          <span>{PROJECT_URL}</span>
        </a>
      </div>
      <div className="project-footer-item">
        <span>论文订阅</span>
        <a href="/rss.xml" target="_blank" rel="noreferrer" title="订阅 ArxivLens RSS">
          <Rss size={15} />
          <span>RSS 每日论文</span>
        </a>
      </div>
    </footer>
  )
}
