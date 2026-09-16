import { GitFork } from 'lucide-react'

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
    </footer>
  )
}
