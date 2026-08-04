"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Loader2, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  listMockEquipmentSchedules,
  updateMockEquipmentScheduleOccupancy,
  type MockEquipment,
  type MockEquipmentSchedule,
} from "@/lib/api";

const DAYS_IN_WEEK = 7;
const HOURS_IN_WEEK = DAYS_IN_WEEK * 24;
type ScheduleView = "year" | "month" | "week";

const VIEW_OPTIONS: Array<{ value: ScheduleView; label: string }> = [
  { value: "year", label: "年" },
  { value: "month", label: "月" },
  { value: "week", label: "周" },
];

function startOfWeek(date: Date) {
  const monday = new Date(date);
  const day = monday.getDay() || 7;
  monday.setDate(monday.getDate() - day + 1);
  monday.setHours(0, 0, 0, 0);
  return monday;
}

function addDays(date: Date, days: number) {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

function addWeeks(date: Date, weeks: number) {
  return addDays(date, weeks * DAYS_IN_WEEK);
}

function formatDateParam(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function isSameDay(left: Date, right: Date) {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate();
}

function overlapHours(item: MockEquipmentSchedule, start: Date, end: Date) {
  const overlapStart = Math.max(new Date(item.start_at).getTime(), start.getTime());
  const overlapEnd = Math.min(new Date(item.end_at).getTime(), end.getTime());
  return Math.max(0, overlapEnd - overlapStart) / 3_600_000;
}

function activityColor(activity: MockEquipmentSchedule["activity_type"]) {
  if (activity === "cleaning") return "bg-amber-500";
  if (activity === "maintenance") return "bg-red-500";
  if (activity === "setup") return "bg-violet-500";
  return "bg-blue-500";
}

function YearSchedule({
  date,
  schedules,
  onSelectMonth,
}: {
  date: Date;
  schedules: MockEquipmentSchedule[];
  onSelectMonth: (date: Date) => void;
}) {
  const months = Array.from({ length: 12 }, (_, monthIndex) => {
    const start = new Date(date.getFullYear(), monthIndex, 1);
    const end = new Date(date.getFullYear(), monthIndex + 1, 1);
    const occupiedHours = schedules.reduce((sum, item) => sum + overlapHours(item, start, end), 0);
    const capacityHours = (end.getTime() - start.getTime()) / 3_600_000;
    return {
      label: `${monthIndex + 1}月`,
      value: Math.min(100, Math.round(occupiedHours / capacityHours * 100)),
      taskCount: schedules.filter((item) => item.activity_type === "production" && overlapHours(item, start, end) > 0).length,
    };
  });

  return (
    <div className="rounded-lg border p-4">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm font-semibold">{date.getFullYear()} 年设备负载</p>
        <p className="text-xs text-muted-foreground">来自 Mock 排期数据</p>
      </div>
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        {months.map((month, monthIndex) => (
          <button
            key={month.label}
            type="button"
            className="rounded-md bg-muted/30 p-3 text-left transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onSelectMonth(new Date(date.getFullYear(), monthIndex, 1))}
            aria-label={`查看 ${date.getFullYear()} 年 ${month.label}排期`}
          >
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium">{month.label}</span>
              <span className="text-muted-foreground">{month.value}%</span>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-emerald-100">
              <div className="h-full rounded-full bg-blue-500" style={{ width: `${month.value}%` }} />
            </div>
            <p className="mt-2 text-[10px] text-muted-foreground">{month.taskCount} 个生产任务</p>
          </button>
        ))}
      </div>
    </div>
  );
}

function MonthSchedule({
  date,
  schedules,
  onSelectDay,
  onEditDay,
}: {
  date: Date;
  schedules: MockEquipmentSchedule[];
  onSelectDay: (date: Date) => void;
  onEditDay: (date: Date, productCode: string | null) => void;
}) {
  const firstDay = new Date(date.getFullYear(), date.getMonth(), 1);
  const daysInMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  const leadingDays = (firstDay.getDay() + 6) % 7;
  const cells = Array.from({ length: leadingDays + daysInMonth }, (_, index) => {
    const day = index - leadingDays + 1;
    if (day <= 0) return null;
    const start = new Date(date.getFullYear(), date.getMonth(), day);
    const end = addDays(start, 1);
    const daySchedules = schedules.filter((item) => overlapHours(item, start, end) > 0);
    const occupiedHours = daySchedules.reduce((sum, item) => sum + overlapHours(item, start, end), 0);
    const primaryProduction = daySchedules
      .filter((item) => item.activity_type === "production")
      .sort((left, right) => overlapHours(right, start, end) - overlapHours(left, start, end))[0];
    return {
      day,
      value: Math.min(100, Math.round(occupiedHours / 24 * 100)),
      taskCount: daySchedules.filter((item) => item.activity_type === "production").length,
      hasCleaning: daySchedules.some((item) => item.activity_type === "cleaning"),
      productCode: primaryProduction?.product_code ?? null,
    };
  });

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="border-b bg-muted/20 px-4 py-3 text-sm font-semibold">
        {date.getFullYear()} 年 {date.getMonth() + 1} 月
      </div>
      <div className="grid grid-cols-7 border-b bg-muted/20 text-center text-xs text-muted-foreground">
        {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((day) => (
          <div key={day} className="border-r py-2 last:border-r-0">{day}</div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {cells.map((cell, index) => (
          <div key={index} className="min-h-24 border-b border-r last:border-r-0">
            {cell ? (
              <div className="relative h-full min-h-24 p-2 transition-colors hover:bg-primary/5">
                <div className="flex items-center justify-between gap-1">
                  <p className="text-xs font-medium">{cell.day}</p>
                  <button
                    type="button"
                    className="flex size-6 items-center justify-center rounded-sm text-muted-foreground hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    onClick={() => onEditDay(new Date(date.getFullYear(), date.getMonth(), cell.day), cell.productCode)}
                    aria-label={`修改 ${date.getMonth() + 1} 月 ${cell.day} 日产品占用`}
                    title="修改产品占用"
                  >
                    <Pencil className="size-3" />
                  </button>
                </div>
                {cell.productCode && <p className="mt-1 truncate text-[9px] font-medium text-primary">{cell.productCode}</p>}
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-emerald-100" title={`占用率 ${cell.value}%`}>
                  <div className="h-full rounded-full bg-blue-500" style={{ width: `${cell.value}%` }} />
                </div>
                <p className="mt-1 text-[9px] text-muted-foreground">占用 {cell.value}%</p>
                <div className="mt-1 flex items-center gap-1 text-[9px] text-muted-foreground">
                  <span>{cell.taskCount} 个任务</span>
                  {cell.hasCleaning && <span className="size-1.5 rounded-full bg-amber-500" title="含清洗排期" />}
                </div>
                <button
                  type="button"
                  className="absolute inset-x-0 bottom-0 h-6 text-[9px] text-muted-foreground hover:text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                  onClick={() => onSelectDay(new Date(date.getFullYear(), date.getMonth(), cell.day))}
                  aria-label={`查看 ${date.getMonth() + 1} 月 ${cell.day} 日所在周的排期`}
                >
                  查看周排期
                </button>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function WeekSchedule({
  equipment,
  weekStart,
  focusedDate,
  schedules,
}: {
  equipment: MockEquipment;
  weekStart: Date;
  focusedDate: Date | null;
  schedules: MockEquipmentSchedule[];
}) {
  const now = new Date();
  const days = Array.from({ length: DAYS_IN_WEEK }, (_, index) => addDays(weekStart, index));
  const weekEnd = addDays(weekStart, DAYS_IN_WEEK);
  const currentHour = (now.getTime() - weekStart.getTime()) / 3_600_000;
  const currentPosition = Math.min(100, Math.max(0, currentHour / HOURS_IN_WEEK * 100));
  const visibleSchedules = schedules
    .filter((item) => overlapHours(item, weekStart, weekEnd) > 0)
    .map((item, index) => {
      const start = Math.max(new Date(item.start_at).getTime(), weekStart.getTime());
      const end = Math.min(new Date(item.end_at).getTime(), weekEnd.getTime());
      return {
        item,
        startHour: (start - weekStart.getTime()) / 3_600_000,
        duration: (end - start) / 3_600_000,
        lane: index % 3,
      };
    });

  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <div className="min-w-[820px]">
        <div className="grid grid-cols-[150px_1fr] border-b bg-muted/20">
          <div className="flex items-center border-r px-4 text-xs font-semibold">设备 / 产线</div>
          <div className="grid grid-cols-7">
            {days.map((day) => (
              <div
                key={day.toISOString()}
                className={`border-r px-2 py-2 text-center last:border-r-0 ${focusedDate && isSameDay(day, focusedDate) ? "bg-primary/10 ring-2 ring-inset ring-primary/40" : ""}`}
                aria-current={focusedDate && isSameDay(day, focusedDate) ? "date" : undefined}
              >
                <p className="text-xs font-semibold">
                  {day.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}
                  <span className="ml-1 text-muted-foreground">{day.toLocaleDateString("zh-CN", { weekday: "short" })}</span>
                </p>
                <div className="mt-1 flex justify-between text-[9px] text-muted-foreground"><span>00:00</span><span>08:00</span><span>16:00</span></div>
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-[150px_1fr]">
          <div className="flex items-center gap-3 border-r px-4 py-5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-xs font-semibold text-primary">{equipment.workshop_code}</span>
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold">{equipment.equipment_id}</p>
              <p className="mt-1 truncate text-[10px] text-muted-foreground">{equipment.label}</p>
              <p className="mt-1 text-[10px] text-muted-foreground">00:00–24:00</p>
            </div>
          </div>
          <div className="relative h-44 overflow-hidden">
            <div className="absolute inset-0 grid grid-cols-7">
              {days.map((day) => (
                <div key={day.toISOString()} className="relative border-r last:border-r-0">
                  <span className="absolute inset-y-0 left-1/3 border-l border-dashed border-border/70" />
                  <span className="absolute inset-y-0 left-2/3 border-l border-dashed border-border/70" />
                </div>
              ))}
            </div>
            {currentHour >= 0 && currentHour <= HOURS_IN_WEEK && (
              <div className="absolute inset-y-0 z-20 border-l-2 border-blue-400" style={{ left: `${currentPosition}%` }} aria-label="当前时间">
                <span className="absolute -left-px top-0 -translate-x-1/2 whitespace-nowrap bg-blue-50 px-1 text-[9px] font-medium text-blue-600">当前时间</span>
              </div>
            )}
            {visibleSchedules.map(({ item, startHour, duration, lane }) => {
              const start = new Date(item.start_at);
              const end = new Date(item.end_at);
              const label = item.activity_type === "cleaning"
                ? `清洗 · ${item.batch_no}`
                : `${item.product_name} · ${item.batch_no}`;
              const detail = `${item.task_name}\n${item.product_name ?? "-"} · ${item.batch_no ?? "-"}\n${start.toLocaleString("zh-CN")}—${end.toLocaleString("zh-CN")}\n${item.operator_team ?? ""}`;
              return (
                <div key={item.id} className="absolute z-10" style={{ left: `${startHour / HOURS_IN_WEEK * 100}%`, top: `${34 + lane * 47}px`, width: `${duration / HOURS_IN_WEEK * 100}%` }} title={detail}>
                  <p className="mb-1 truncate text-[9px] font-medium">{label}</p>
                  <div className={`h-3 min-w-3 rounded-sm ${activityColor(item.activity_type)}`}>
                    {item.status === "running" && <span className="float-right mr-1 mt-0.5 block size-2 rounded-full bg-lime-300 ring-1 ring-white" />}
                  </div>
                  <p className="mt-0.5 truncate text-[8px] text-muted-foreground">{start.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}–{end.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</p>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

export function EquipmentWeekSchedule({ equipment }: { equipment: MockEquipment }) {
  const queryClient = useQueryClient();
  const now = new Date();
  const [view, setView] = useState<ScheduleView>("week");
  const [weekAnchor, setWeekAnchor] = useState(() => startOfWeek(now));
  const [focusedDate, setFocusedDate] = useState<Date | null>(null);
  const [editingDate, setEditingDate] = useState<Date | null>(null);
  const [editingProduct, setEditingProduct] = useState<"HRS-5678" | "HRS-1597">("HRS-5678");
  const displayDate = focusedDate ?? now;
  const weekEnd = addDays(weekAnchor, 6);
  const isCurrentWeek = weekAnchor.getTime() === startOfWeek(now).getTime();

  let rangeStart: Date;
  let rangeEnd: Date;
  if (view === "year") {
    rangeStart = new Date(displayDate.getFullYear(), 0, 1);
    rangeEnd = new Date(displayDate.getFullYear(), 11, 31);
  } else if (view === "month") {
    rangeStart = new Date(displayDate.getFullYear(), displayDate.getMonth(), 1);
    rangeEnd = new Date(displayDate.getFullYear(), displayDate.getMonth() + 1, 0);
  } else {
    rangeStart = weekAnchor;
    rangeEnd = weekEnd;
  }
  const startDate = formatDateParam(rangeStart);
  const endDate = formatDateParam(rangeEnd);
  const scheduleQuery = useQuery({
    queryKey: ["mock", "equipment-schedules", equipment.equipment_id, startDate, endDate],
    queryFn: () => listMockEquipmentSchedules(equipment.equipment_id, startDate, endDate),
  });
  const schedules = scheduleQuery.data ?? [];
  const occupancyMutation = useMutation({
    mutationFn: updateMockEquipmentScheduleOccupancy,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["mock", "equipment-schedules", equipment.equipment_id],
      });
      setEditingDate(null);
    },
  });

  return (
    <section className="space-y-3" aria-labelledby="equipment-week-schedule-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 id="equipment-week-schedule-title" className="flex items-center gap-2 text-sm font-semibold">
            <CalendarDays className="size-4 text-primary" />
            模拟设备排期
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">生产任务、产品批次与设备占用 · Mock API 数据</p>
        </div>
        <div className="flex items-center gap-1 rounded-md border bg-muted/20 p-1" role="tablist" aria-label="排期视图">
          {VIEW_OPTIONS.map((option) => (
            <Button key={option.value} type="button" size="sm" variant={view === option.value ? "default" : "ghost"} role="tab" aria-selected={view === option.value} onClick={() => setView(option.value)}>{option.label}</Button>
          ))}
        </div>
      </div>

      {view === "week" && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/20 px-2 py-1.5">
          <div className="flex items-center gap-1">
            <Button type="button" variant="ghost" size="icon" aria-label="上一周" onClick={() => { setFocusedDate(null); setWeekAnchor((date) => addWeeks(date, -1)); }}><ChevronLeft /></Button>
            <Button type="button" variant="outline" size="sm" disabled={isCurrentWeek} onClick={() => { setFocusedDate(null); setWeekAnchor(startOfWeek(new Date())); }}>本周</Button>
            <Button type="button" variant="ghost" size="icon" aria-label="下一周" onClick={() => { setFocusedDate(null); setWeekAnchor((date) => addWeeks(date, 1)); }}><ChevronRight /></Button>
          </div>
          <p className="text-xs font-medium">{weekAnchor.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" })}<span className="mx-1 text-muted-foreground">—</span>{weekEnd.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}</p>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-blue-500" />生产</span>
            <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-amber-500" />清洗</span>
          </div>
        </div>
      )}

      {scheduleQuery.isLoading && <div className="flex h-32 items-center justify-center rounded-lg border text-sm text-muted-foreground"><Loader2 className="mr-2 size-4 animate-spin" />加载排期数据…</div>}
      {scheduleQuery.isError && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">排期数据加载失败：{String(scheduleQuery.error)}</div>}
      {scheduleQuery.isSuccess && view === "year" && <YearSchedule date={displayDate} schedules={schedules} onSelectMonth={(date) => { setFocusedDate(date); setView("month"); }} />}
      {scheduleQuery.isSuccess && view === "month" && (
        <MonthSchedule
          date={displayDate}
          schedules={schedules}
          onSelectDay={(date) => { setFocusedDate(date); setWeekAnchor(startOfWeek(date)); setView("week"); }}
          onEditDay={(date, productCode) => {
            setEditingDate(date);
            setEditingProduct(productCode === "HRS-1597" ? "HRS-1597" : "HRS-5678");
            occupancyMutation.reset();
          }}
        />
      )}
      {scheduleQuery.isSuccess && view === "week" && <WeekSchedule equipment={equipment} weekStart={weekAnchor} focusedDate={focusedDate} schedules={schedules} />}

      <Dialog open={editingDate !== null} onOpenChange={(open) => { if (!open) setEditingDate(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改产品占用</DialogTitle>
            <DialogDescription>
              {editingDate?.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric" })}
              {" · "}{equipment.equipment_id} {equipment.label}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <label className="text-sm font-medium" htmlFor="schedule-product">占用产品</label>
            <Select value={editingProduct} onValueChange={(value) => setEditingProduct(value as "HRS-5678" | "HRS-1597")}>
              <SelectTrigger id="schedule-product">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="HRS-5678">HRS-5678</SelectItem>
                <SelectItem value="HRS-1597">HRS-1597</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">保存后，该产品将占用所选日期的完整 24 小时。</p>
            {occupancyMutation.isError && <p className="text-sm text-destructive">保存失败：{String(occupancyMutation.error)}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setEditingDate(null)} disabled={occupancyMutation.isPending}>取消</Button>
            <Button
              type="button"
              disabled={!editingDate || occupancyMutation.isPending}
              onClick={() => {
                if (!editingDate) return;
                occupancyMutation.mutate({
                  equipment_id: equipment.equipment_id,
                  schedule_date: formatDateParam(editingDate),
                  product_code: editingProduct,
                });
              }}
            >
              {occupancyMutation.isPending && <Loader2 className="animate-spin" />}
              保存占用
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
