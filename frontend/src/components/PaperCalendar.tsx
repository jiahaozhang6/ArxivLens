import { useEffect, useRef, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import type { PaperDateItem } from '../types'

type PaperCalendarProps = {
  value: string
  today: string
  dates: PaperDateItem[]
  onSelect: (date: string) => void
}

function shiftMonth(month: string, amount: number) {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, number - 1 + amount, 1)).toISOString().slice(0, 7)
}

function monthDays(month: string) {
  const [year, number] = month.split('-').map(Number)
  return {
    offset: new Date(Date.UTC(year, number - 1, 1)).getUTCDay(),
    count: new Date(Date.UTC(year, number, 0)).getUTCDate(),
  }
}

export function PaperCalendar({ value, today, dates, onSelect }: PaperCalendarProps) {
  const [open, setOpen] = useState(false)
  const [month, setMonth] = useState((value || today).slice(0, 7))
  const root = useRef<HTMLDivElement>(null)
  const counts = new Map(dates.map((item) => [item.date, item.count]))
  const { offset, count } = monthDays(month)
  const [year, number] = month.split('-').map(Number)
  const label = value ? new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric',
  }).format(new Date(value + 'T12:00:00')) : '选择日期'

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="paper-calendar" ref={root}>
      <button type="button" className="paper-calendar-trigger"
        aria-label={'选择论文日期，当前' + label} aria-expanded={open}
        onClick={() => {
          if (!open) setMonth((value || today).slice(0, 7))
          setOpen(!open)
        }}
      ><CalendarDays size={16} /><span>{label}</span></button>
      {open && (
        <div className="paper-calendar-popover" role="dialog" aria-label="选择论文日期">
          <div className="paper-calendar-heading">
            <button type="button" className="icon-button" onClick={() => setMonth(shiftMonth(month, -1))} aria-label="上个月"><ChevronLeft size={17} /></button>
            <strong>{year}年{number}月</strong>
            <button type="button" className="icon-button" onClick={() => setMonth(shiftMonth(month, 1))} disabled={month >= today.slice(0, 7)} aria-label="下个月"><ChevronRight size={17} /></button>
          </div>
          <div className="paper-calendar-grid" role="group" aria-label={year + '年' + number + '月'}>
            {['日', '一', '二', '三', '四', '五', '六'].map((weekday) => <span className="paper-calendar-weekday" key={weekday}>{weekday}</span>)}
            {Array.from({ length: 42 }, (_, index) => {
              const day = index - offset + 1
              if (day < 1 || day > count) return <span key={index} />
              const date = month + '-' + String(day).padStart(2, '0')
              const papers = counts.get(date) ?? 0
              return <button type="button" key={date}
                className={'paper-calendar-day' + (papers ? ' has-papers' : '') + (date === value ? ' selected' : '') + (date === today ? ' today' : '')}
                disabled={date > today}
                title={date + (papers ? ' · ' + papers + ' 篇论文' : ' · 暂无论文')}
                aria-label={date + (papers ? '，' + papers + ' 篇论文' : '，暂无论文')}
                aria-current={date === today ? 'date' : undefined}
                aria-pressed={date === value}
                onClick={() => { onSelect(date); setOpen(false) }}
              >{day}</button>
            })}
          </div>
          <div className="paper-calendar-footer">
            <span><i /> 有论文</span>
            <button type="button" onClick={() => { onSelect(today); setOpen(false) }}>今天</button>
          </div>
        </div>
      )}
    </div>
  )
}
