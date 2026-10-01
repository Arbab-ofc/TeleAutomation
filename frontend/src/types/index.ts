export type Account = { id:string; first_name:string|null; last_name:string|null; username:string|null; phone:string|null }
export type TelegramStatus = { configured:boolean; connected:boolean; authorized:boolean; state:string; account:Account|null; last_error:string|null }
export type Dialog = { id:string; title:string; username:string|null; type:'group'|'supergroup'|'channel'|'private'; can_send:boolean }
export type Template = { id:string; name:string; content:string }
export type AutomationStatus = { state:'STOPPED'|'STARTING'|'RUNNING'|'WAITING'|'RATE_LIMITED'|'PAUSED'|'STOPPING'|'ERROR'|'COMPLETED'; running:boolean; paused:boolean; chat_id:string|null; chat_title:string|null; chat_ids:string[]; chat_titles:string[]; message:string|null; interval_seconds:number|null; started_at:string|null; start_at:string|null; last_sent_at:string|null; next_send_at:string|null; sent_count:number; failed_count:number; active_weekdays:number[]; window_start:string|null; window_end:string|null; timezone_offset_minutes:number; max_messages:number|null; last_error:string|null }
export type AutomationSchedule = { start_at:string|null; active_weekdays:number[]; window_start:string|null; window_end:string|null; timezone_offset_minutes:number; max_messages:number|null }
export type LogEntry = { timestamp:string; level:'INFO'|'SUCCESS'|'WARNING'|'ERROR'; event:string; message:string; destination:string|null; error:string|null }
export type Settings = { configured:boolean; api_id:string|null; api_hash:null; minimum_interval_seconds:number }
