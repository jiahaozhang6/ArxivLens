import { BookOpenText, GitFork } from 'lucide-react'

const PROJECT_URL = 'https://github.com/jiahaozhang6/ArxivLens'
const READER_URL = 'https://58.87.103.33:8888/'

export function ProjectFooter() {
  return (
    <footer className="project-footer">
      <div className="project-footer-item">
        <span>每日论文阅读入口</span>
        <a href={READER_URL} target="_blank" rel="noreferrer" title="打开每日论文阅读入口">
          <BookOpenText size={15} />
          <span>{READER_URL}</span>
        </a>
      </div>
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
