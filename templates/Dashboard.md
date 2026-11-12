# Project Dashboard

Static tracker output: [dashboard.html](file:///C:/repos/Projects/ProjectHub/output/dashboard.html)

## Overdue

```tasks
not done
due before today
sort by due
group by filename
```

## Due in next 7 days

```tasks
not done
due after yesterday
due before in 8 days
sort by due
group by filename
```

## No due date (open)

```tasks
not done
no due date
sort by filename
```

## Projects

```dataview
TABLE customer, project_no, so, qte, stage, assigned FROM "" WHERE project_no SORT assigned DESC
```

## Open tasks

```dataview
TASK FROM "" WHERE !completed SORT due ASC
```
